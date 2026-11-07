import json
import re

import httpx

from app.cv import assemble_cv, template_cover, template_summary

PROFILE_KEYS = ("seeking", "titles", "must_have", "nice_have", "city", "salary_floor_bdt", "work_mode", "exclude")


class ModelError(Exception):
    pass


# Shown when chat or a model-written CV is asked for and settings have no endpoint.
NO_MODEL = "Chat and a model-written CV need a model, and none is configured in this environment."


# Cold llama3.2 on CPU often spends the first minute loading weights.
_CONNECT_SECONDS = 10.0
_READ_SECONDS = 180.0


# Opening a lead must not wait on a cold model. Chat can still use the long read.
_CV_SECONDS = 6.0


def _chat_timeout(timeout: float | None) -> httpx.Timeout:
    if timeout is None:
        return httpx.Timeout(_READ_SECONDS, connect=_CONNECT_SECONDS, pool=_CONNECT_SECONDS)
    read = max(float(timeout), 1.0)
    connect = min(_CONNECT_SECONDS, read)
    return httpx.Timeout(read, connect=connect, pool=connect)


def _grounded(text: str, profile: dict) -> bool:
    """Reject model prose that adds a year or degree the resume does not contain."""
    resume = profile.get("resume_text") or ""
    resume_l = resume.lower()
    for year in re.findall(r"\b(?:19|20)\d{2}\b", text):
        if year not in resume:
            return False
    for word in ("phd", "ph.d", "mba", "bachelor", "b.sc", "bsc", "m.sc", "msc", "doctorate"):
        if re.search(rf"(?<![a-z.]){re.escape(word)}(?![a-z])", text.lower()):
            if not re.search(rf"(?<![a-z.]){re.escape(word)}(?![a-z])", resume_l):
                return False
    return True


def _error_text(exc: BaseException) -> str:
    parts: list[str] = []
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen and len(parts) < 6:
        seen.add(id(current))
        text = " ".join(str(current).split())
        if text and text.casefold() not in {part.casefold() for part in parts}:
            parts.append(text)
        current = current.__cause__ or current.__context__
    return ": ".join(parts) if parts else type(exc).__name__


def _connection_refused(exc: BaseException) -> bool:
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if getattr(current, "winerror", None) == 10061 or getattr(current, "errno", None) in {61, 111}:
            return True
        text = str(current).lower()
        if "connection refused" in text or "actively refused" in text:
            return True
        current = current.__cause__
    return False


def configured(settings: dict) -> bool:
    """Check if model is configured (either preset or custom).
    
    Checks for base_url, model, and effective API key (from env var or saved).
    """
    if not (settings.get("base_url") and settings.get("model")):
        return False
    
    # Check for API key (env var or saved)
    if settings.get("use_preset"):
        from app.db import get_effective_api_key
        return bool(get_effective_api_key(settings, settings.get("preset_provider", "")))
    else:
        return bool(settings.get("api_key"))


def _endpoint(base_url: str) -> str:
    base = base_url.strip().rstrip("/")
    if base.endswith("/chat/completions"):
        return base
    if not base.endswith("/v1"):
        base += "/v1"
    return base + "/chat/completions"


def _parse_json(text: str) -> dict:
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start < 0 or end <= start:
            raise
        parsed = json.loads(cleaned[start : end + 1])
    if not isinstance(parsed, dict):
        raise json.JSONDecodeError("object required", cleaned, 0)
    return parsed


async def complete(settings: dict, messages: list[dict], timeout: float | None = None) -> str:
    if not configured(settings):
        raise ModelError(NO_MODEL)
    headers = {"Content-Type": "application/json"}
    
    # Get effective API key (from env var or saved settings)
    if settings.get("use_preset"):
        from app.db import get_effective_api_key
        api_key = get_effective_api_key(settings, settings.get("preset_provider", ""))
    else:
        api_key = settings.get("api_key", "")
    
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    
    payload = {
        "model": settings["model"],
        "temperature": 0.2,
        "messages": messages,
    }
    try:
        async with httpx.AsyncClient(timeout=_chat_timeout(timeout)) as client:
            response = await client.post(_endpoint(settings["base_url"]), headers=headers, json=payload)
            response.raise_for_status()
            body = response.json()
    except (httpx.ReadTimeout, httpx.WriteTimeout) as exc:
        raise ModelError(
            "The model timed out before it finished. The first reply can take a minute while the model loads. Try again."
        ) from exc
    except httpx.ConnectTimeout as exc:
        raise ModelError("Timed out opening a connection to the model. Check the base URL.") from exc
    except httpx.ConnectError as exc:
        if _connection_refused(exc):
            raise ModelError(
                "Connection refused. Nothing is listening at that address. Start Ollama, then try again."
            ) from exc
        raise ModelError("Could not connect to the model. Check the base URL and that Ollama is running.") from exc
    except httpx.HTTPStatusError as exc:
        code = exc.response.status_code
        snippet = " ".join((exc.response.text or "").split())[:180]
        if code == 404:
            detail = "That model was not found. Check the model name in Settings."
        else:
            detail = f"The model returned an error (HTTP {code})."
        if snippet:
            detail = f"{detail} {snippet}"
        raise ModelError(detail) from exc
    except httpx.HTTPError as exc:
        raise ModelError(_error_text(exc)) from exc
    try:
        return body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ModelError("The model response did not include a message.") from exc


async def requirements_turn(settings: dict, history: list[dict], profile: dict, user_message: str) -> tuple[str, dict]:
    current = {key: profile.get(key) for key in PROFILE_KEYS}
    resume = (profile.get("resume_text") or "")[:8000]
    system = (
        "You help a job seeker in Bangladesh turn a conversation into search requirements. "
        "Talk in short, plain sentences. Return only a JSON object with this shape: "
        '{"reply":"what you say","profile":{"seeking":[],"titles":[],"must_have":[],"nice_have":[],'
        '"city":"Dhaka","salary_floor_bdt":null,"work_mode":"both","exclude":[]}}. '
        "seeking is the kind of job they want now, in their words, such as junior Python or data analyst. "
        "Search keeps listings of that kind, so put the role they asked for in seeking and nowhere else. "
        "Replace seeking when they name a different kind of job. "
        "Omit seeking, or repeat the current list, when they did not name a role. "
        "titles are past roles already on the profile. Change titles only when they correct their history. "
        "Keep every other current profile value unless the user changed it. "
        "work_mode is remote only when they want remote jobs alone, otherwise both. "
        "Do not put remote into seeking. "
        "salary_floor_bdt is an integer in taka or null. "
        "Put a tool or skill in must_have only when they say they know it or point at the resume. "
        "Do not invent employers, dates, or skills. "
        "In reply, name the kind of job you will search for. "
        "Ask one short follow-up when they have not named a role yet. "
        'Example: "I want junior Python work in Dhaka and I know Django" '
        'sets seeking to ["Junior Python"], must_have to include Django, city to Dhaka, and work_mode to both.'
    )
    messages = [
        {"role": "system", "content": system},
        {
            "role": "system",
            "content": "Current profile:\n" + json.dumps(current) + "\n\nResume:\n" + resume,
        },
        *[{"role": item["role"], "content": item["content"]} for item in history if item["role"] in {"user", "assistant"}],
        {"role": "user", "content": user_message},
    ]
    text = await complete(settings, messages)
    try:
        parsed = _parse_json(text)
    except json.JSONDecodeError:
        messages.append({"role": "assistant", "content": text})
        messages.append({"role": "user", "content": "Return only the JSON object, with no other text."})
        text = await complete(settings, messages)
        parsed = _parse_json(text)
    reply = str(parsed.get("reply") or "").strip() or "I updated the form from what you said."
    patch = parsed.get("profile") if isinstance(parsed.get("profile"), dict) else {}
    lifted = dict(patch)
    for key in PROFILE_KEYS:
        if key not in lifted and key in parsed:
            lifted[key] = parsed[key]
    return reply, {key: lifted[key] for key in PROFILE_KEYS if key in lifted}


async def write_summary_and_note(settings: dict, profile: dict, job: dict) -> tuple[str, str]:
    resume = (profile.get("resume_text") or "")[:8000]
    description = (job.get("description") or "")[:5000]
    system = (
        "Write a two-sentence CV summary and a short cover note for one job. "
        "Use only facts from the resume. Do not invent employers, dates, degrees, or skills. "
        "The summary must mention the job title and must not claim a job, employer, date, or degree "
        "that the resume does not already state. Do not say the person worked at the hiring company. "
        'Return only JSON: {"summary":"...","cover_note":"..."}. '
        "The cover note is a letter the person can copy. Address the company by the name given here."
    )
    user = (
        f"Name: {profile.get('name') or ''}\n"
        f"Contact: {profile.get('contact') or ''}\n"
        f"City: {profile.get('city') or ''}\n"
        f"Job title: {job.get('title') or ''}\n"
        f"Company: {job.get('company') or ''}\n"
        f"Job text:\n{description}\n\nResume:\n{resume}"
    )
    try:
        text = await complete(
            settings,
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            timeout=_CV_SECONDS,
        )
        parsed = _parse_json(text)
        summary = str(parsed.get("summary") or "").strip()
        note = str(parsed.get("cover_note") or "").strip()
        if not summary or not note or not _grounded(summary, profile) or not _grounded(note, profile):
            raise ModelError("incomplete")
        return summary, note
    except (ModelError, json.JSONDecodeError, TimeoutError):
        return template_summary(profile, job), template_cover(profile, job)


async def build_documents(settings: dict, profile: dict, job: dict) -> tuple[str, str]:
    if configured(settings):
        summary, note = await write_summary_and_note(settings, profile, job)
    else:
        summary, note = template_summary(profile, job), template_cover(profile, job)
    return assemble_cv(profile, job, summary), note
