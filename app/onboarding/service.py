"""Onboarding steps, profile versions, and the action gate."""

from __future__ import annotations

from app import db
from app.llm import configured
from app.onboarding import store
from app.onboarding.documents import all_experiences, build_source
from app.onboarding.filters import normalize_constraints
from app.onboarding.infer import infer_from_source
from app.onboarding.profile import (
    STATUS_LABELS,
    apply_swipe,
    assemble_fields,
    decided_weight,
    gate_decision,
    is_major,
    is_stable,
)
from app.onboarding.reflect import build_questions, items_from_answers
from app.resume import parse_resume

ACTION_NOTES = {
    "match": "Matching stays off in this action. Discover reads the preference profile when you open it.",
    "resume-tailor": "Resume tailoring stays off. This does not edit a resume.",
    "auto-prepare": "Auto-prepared applications stay off. This does not apply to a job.",
}


class OnboardingError(Exception):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def _applied_jobs() -> list[dict]:
    try:
        return db.list_jobs(["applied"])
    except Exception:
        return []


def _source() -> tuple[str, list[dict]]:
    profile = db.get_profile()
    applied = _applied_jobs()
    resume = str(profile.get("resume_text") or "")
    return build_source(resume, applied), all_experiences(resume, applied)


def snapshot() -> dict:
    state = store.get_constraints()
    versions = store.list_versions()
    latest = versions[-1] if versions else None
    stable = is_stable(versions)
    profile = db.get_profile()
    history = []
    for version in versions:
        history.append(
            {
                "version": version["version"],
                "created_at": version["created_at"],
                "reason": version["reason"],
                "major_change": version["major_change"],
                "signature": (version["data"] or {}).get("signature") or "",
                "fields": (version["data"] or {}).get("fields") or [],
            }
        )
    return {
        "ok": True,
        "status": state["status"],
        "status_label": STATUS_LABELS.get(state["status"], state["status"]),
        "constraints": state["filters"],
        "inference_note": state["inference_note"],
        "has_resume": bool(str(profile.get("resume_text") or "").strip()),
        "model_configured": configured(db.get_settings()),
        "items": store.list_items(),
        "questions": store.list_questions(),
        "answers": store.list_answers(),
        "profile": None
        if latest is None
        else {
            "version": latest["version"],
            "created_at": latest["created_at"],
            "reason": latest["reason"],
            "major_change": latest["major_change"],
            "fields": (latest["data"] or {}).get("fields") or [],
            "signature": (latest["data"] or {}).get("signature") or "",
        },
        "history": history,
        "stable": stable,
        "gate": gate_decision(stable, state["actions_override"]),
    }


def save_constraints(payload: dict) -> dict:
    try:
        filters = normalize_constraints(payload)
    except ValueError as exc:
        raise OnboardingError(400, str(exc)) from exc
    current = store.get_constraints()
    status = current["status"]
    if status == "not_started":
        status = "constraints_done"
    store.save_filters(filters, status=status)
    if store.latest_version() is not None:
        _write_version("constraints")
    return snapshot()


def save_resume_text(text: str) -> dict:
    cleaned = str(text or "").strip()
    if not cleaned:
        raise OnboardingError(400, "Paste resume text, or upload a PDF.")
    parsed = parse_resume(cleaned)
    current = db.get_profile()
    update = {
        "resume_text": cleaned,
        "name": parsed["name"] or current.get("name") or "",
        "contact": parsed["contact"] or current.get("contact") or "",
    }
    if parsed["skills"] and not current.get("must_have"):
        update["must_have"] = parsed["skills"]
    if parsed["titles"] and not current.get("titles"):
        update["titles"] = parsed["titles"]
    db.save_profile(update)
    return snapshot()


async def infer() -> dict:
    state = store.get_constraints()
    if state["status"] == "not_started":
        raise OnboardingError(409, "Save constraints before building the profile. You can leave them blank.")
    source, experiences = _source()
    try:
        result = await infer_from_source(source, db.get_settings(), experiences)
    except OnboardingError:
        raise
    except Exception as exc:
        raise OnboardingError(502, "The profile could not be read. Try again.") from exc
    store.set_inference_note(result["label"])
    store.delete_proposed("inferred")
    for item in result["items"]:
        existing = store.get_item(item["key"])
        if existing and existing["status"] != "proposed":
            continue
        store.upsert_item(item)
    body = snapshot()
    body["inference"] = {
        "writer": result["writer"],
        "label": result["label"],
        "attempts": result["attempts"],
    }
    return body


def _require_item(key: str) -> dict:
    item = store.get_item(key)
    if item is None:
        raise OnboardingError(404, "That line is not in the review.")
    return item


def decide_item(key: str, decision: str) -> dict:
    if decision not in {"confirmed", "rejected"}:
        raise OnboardingError(400, "Choose confirm or reject.")
    item = _require_item(key)
    if item["locked"]:
        if decision == "confirmed" and item["status"] == "confirmed":
            return snapshot()
        raise OnboardingError(409, "That line is locked. Unlock it before changing it.")
    weight, locked = decided_weight(item, decision)
    item["status"] = decision
    item["weight"] = weight
    item["locked"] = locked
    item["polarity"] = "prefer" if weight > 0 else "avoid"
    store.upsert_item(item)
    return snapshot()


def edit_item(key: str, name: str) -> dict:
    item = _require_item(key)
    if item["locked"]:
        raise OnboardingError(409, "That line is locked. Unlock it before changing it.")
    cleaned = " ".join(str(name or "").split())
    if not cleaned:
        raise OnboardingError(400, "Write a name before saving the edit.")
    item["name"] = cleaned[:160]
    item["edited"] = True
    store.upsert_item(item)
    return snapshot()


def unlock_item(key: str) -> dict:
    item = _require_item(key)
    item["locked"] = False
    if item["status"] == "confirmed":
        item["status"] = "proposed"
        item["weight"] = None
    store.upsert_item(item)
    if store.latest_version() is not None:
        _write_version("unlock")
    return snapshot()


def _previous_fields() -> list[dict]:
    latest = store.latest_version()
    if latest is None:
        return []
    return list((latest.get("data") or {}).get("fields") or [])


def _write_version(reason: str) -> dict:
    state = store.get_constraints()
    fields = assemble_fields(store.list_items(), state["filters"], _previous_fields())
    previous = _previous_fields()
    major = is_major(previous, fields)
    swipes = []
    latest = store.latest_version()
    if latest is not None:
        swipes = list((latest.get("data") or {}).get("swipes") or [])
    from app.onboarding.profile import signature

    document = {
        "fields": fields,
        "signature": signature(fields),
        "swipes": swipes,
        "onboarding_status": state["status"],
    }
    return store.insert_version(document, major_change=major, reason=reason)


def continue_review() -> dict:
    state = store.get_constraints()
    if state["status"] not in {"constraints_done", "profile_confirmed"}:
        raise OnboardingError(409, "Save your constraints before confirming the profile.")
    store.set_status("profile_confirmed")
    _write_version("review")
    return snapshot()


def prepare_reflection() -> dict:
    state = store.get_constraints()
    if state["status"] not in {"profile_confirmed", "reflection_done", "complete"}:
        raise OnboardingError(409, "Confirm the profile lines before the reflection questions.")
    existing = store.list_questions()
    if existing:
        return snapshot()
    _resume, experiences = _source()
    store.replace_questions(build_questions(experiences))
    return snapshot()


def submit_reflection(answers: list[dict]) -> dict:
    state = store.get_constraints()
    if state["status"] not in {"profile_confirmed", "reflection_done"}:
        raise OnboardingError(409, "The reflection step is not open yet.")
    questions = store.list_questions()
    if not questions:
        _resume, experiences = _source()
        questions = build_questions(experiences)
        store.replace_questions(questions)
    provided = {str(answer.get("question_key") or ""): answer for answer in answers}
    complete_answers = []
    for question in questions:
        answer = provided.get(question["question_key"], {"question_key": question["question_key"], "skipped": True})
        answer = dict(answer)
        answer["question_key"] = question["question_key"]
        complete_answers.append(answer)
    try:
        rows, items = items_from_answers(questions, complete_answers)
    except ValueError as exc:
        raise OnboardingError(400, str(exc)) from exc
    store.replace_answers(rows)
    store.delete_proposed("reflection")
    for item in items:
        existing = store.get_item(item["key"])
        if existing and existing["status"] != "proposed":
            continue
        store.upsert_item(item)
    store.set_status("reflection_done")
    return snapshot()


def finish() -> dict:
    state = store.get_constraints()
    if state["status"] not in {"reflection_done", "complete"}:
        raise OnboardingError(409, "Answer the reflection step, or skip it, before finishing.")
    store.set_status("complete")
    _write_version("complete")
    return snapshot()


def record_swipe(verdict: str, posting: dict) -> dict:
    latest = store.latest_version()
    if latest is None:
        raise OnboardingError(409, "Confirm a profile before swipes can update it.")
    choice = str(verdict or "").strip().casefold()
    try:
        fields, signals = apply_swipe(list((latest["data"] or {}).get("fields") or []), choice, posting or {})
    except ValueError as exc:
        raise OnboardingError(400, str(exc)) from exc
    from app.onboarding.profile import signature

    swipes = list((latest["data"] or {}).get("swipes") or [])
    swipes.append(
        {
            "verdict": choice,
            "posting_id": str((posting or {}).get("id") or (posting or {}).get("posting_id") or ""),
            "signals": [signal["name"] for signal in signals],
        }
    )
    major = is_major((latest["data"] or {}).get("fields") or [], fields)
    document = {
        "fields": fields,
        "signature": signature(fields),
        "swipes": swipes,
        "onboarding_status": store.get_constraints()["status"],
    }
    saved = store.insert_version(document, major_change=major, reason=f"swipe:{choice}")
    body = snapshot()
    body["version"] = saved["version"]
    body["major_change"] = saved["major_change"]
    return body


def set_gate(override: str) -> dict:
    try:
        store.set_override(override if override is not None else "")
    except ValueError as exc:
        raise OnboardingError(400, str(exc)) from exc
    return snapshot()


def run_action(name: str) -> dict:
    if name not in ACTION_NOTES:
        raise OnboardingError(404, "That action is not available.")
    body = snapshot()
    gate = body["gate"]
    if not gate["entry_allowed"]:
        raise OnboardingError(403, gate["detail"])
    return {
        "ok": True,
        "action": name,
        "performed": False,
        "stable": body["stable"],
        "gate": gate,
        "detail": ACTION_NOTES[name],
    }


def edit_profile_field(key: str, name: str) -> dict:
    latest = store.latest_version()
    if latest is None:
        raise OnboardingError(404, "There is no preference profile yet.")
    fields = [dict(field) for field in (latest["data"] or {}).get("fields") or []]
    target = next((field for field in fields if field.get("key") == key), None)
    if target is None:
        raise OnboardingError(404, "That field is not in the profile.")
    if target.get("source") == "constraint":
        raise OnboardingError(400, "Edit hard filters on the constraints step.")
    if target.get("locked"):
        raise OnboardingError(409, "That field is locked. Unlock it before changing it.")
    cleaned = " ".join(str(name or "").split())
    if not cleaned:
        raise OnboardingError(400, "Write a name before saving the edit.")
    target["name"] = cleaned[:160]
    target["edited"] = True
    _save_profile_fields(fields, latest, "edit")
    return snapshot()


def lock_profile_field(key: str, locked: bool) -> dict:
    latest = store.latest_version()
    if latest is None:
        raise OnboardingError(404, "There is no preference profile yet.")
    fields = [dict(field) for field in (latest["data"] or {}).get("fields") or []]
    target = next((field for field in fields if field.get("key") == key), None)
    if target is None:
        raise OnboardingError(404, "That field is not in the profile.")
    if target.get("source") == "constraint":
        raise OnboardingError(400, "Hard filters stay on the constraints step.")
    target["locked"] = bool(locked)
    _save_profile_fields(fields, latest, "lock")
    return snapshot()


def _save_profile_fields(fields: list[dict], latest: dict, reason: str) -> None:
    from app.onboarding.profile import signature

    major = is_major((latest["data"] or {}).get("fields") or [], fields)
    document = {
        "fields": fields,
        "signature": signature(fields),
        "swipes": list((latest["data"] or {}).get("swipes") or []),
        "onboarding_status": store.get_constraints()["status"],
    }
    store.insert_version(document, major_change=major, reason=reason)


def first_batch_for(jobs: list[dict], target: int) -> dict:
    from app.onboarding.batch import first_batch

    latest = store.latest_version()
    fields = [] if latest is None else list((latest["data"] or {}).get("fields") or [])
    filters = store.get_constraints()["filters"]
    result = first_batch(jobs, filters, fields, target=target)
    result["ok"] = True
    result["version"] = None if latest is None else latest["version"]
    return result
