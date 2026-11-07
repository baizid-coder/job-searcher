"""Quote-backed reading of a resume, plus experiences for reflection questions."""

from __future__ import annotations

import re

from app.resume import parse_resume

SECTION_KIND = (
    ("professional experience", "job"),
    ("work experience", "job"),
    ("employment history", "job"),
    ("work history", "job"),
    ("internships", "internship"),
    ("internship", "internship"),
    ("experience", "job"),
    ("projects", "project"),
    ("project", "project"),
    ("coursework", "course"),
    ("courses", "course"),
    ("education", "course"),
)

INDUSTRY_WORDS = (
    "healthcare",
    "finance",
    "education",
    "banking",
    "retail",
    "government",
    "hospitality",
    "manufacturing",
)

SENIORITY_WORDS = ("senior", "junior", "intern", "lead", "principal", "entry-level")

DOCUMENT_LABEL = "Document-backed, not model-written."
MODEL_LABEL = "Proposed by the model. Nothing is saved until you confirm it."


def quote_span(source: str, needle: str) -> str:
    if not source or not needle:
        return ""
    index = source.casefold().find(needle.strip().casefold())
    if index < 0:
        return ""
    return source[index : index + len(needle.strip())]


def _header_kind(line: str) -> str | None:
    lowered = line.strip().casefold().rstrip(":")
    for name, kind in sorted(SECTION_KIND, key=lambda item: len(item[0]), reverse=True):
        if lowered == name:
            return kind
    return None


def _is_date_only(line: str) -> bool:
    return bool(re.fullmatch(r"[\d\s\-/–—,|]*(?:19|20)\d{2}[\d\s\-/–—,|]*", line.strip()))


def _bullet(line: str) -> str:
    match = re.match(r"^(?:[-*•]|\d+[.)])\s+(.*\S)\s*$", line.strip())
    return match.group(1).strip() if match else ""


def extract_experiences(text: str) -> list[dict]:
    """Pull jobs, internships, projects, and courses. Bullets stay quotes from the text."""
    raw = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    experiences: list[dict] = []
    kind: str | None = None
    current: dict | None = None

    def flush() -> None:
        nonlocal current
        if current is None:
            return
        bullets = []
        seen: set[str] = set()
        for bullet in current["bullets"]:
            key = bullet.casefold()
            if key in seen:
                continue
            seen.add(key)
            bullets.append(bullet)
        current["bullets"] = bullets[:8]
        if current["name"] and current["name"].casefold() not in {"experience", "project", "education"}:
            experiences.append(current)
        current = None

    for line in raw.split("\n"):
        cleaned = line.strip()
        if not cleaned:
            continue
        header = _header_kind(cleaned)
        if header:
            flush()
            kind = header
            continue
        if kind is None or _is_date_only(cleaned):
            continue
        bullet = _bullet(cleaned)
        if bullet:
            if current is None:
                continue
            current["bullets"].append(bullet)
            continue
        flush()
        item_kind = kind
        if item_kind == "job" and re.search(r"\bintern", cleaned, re.I):
            item_kind = "internship"
        current = {"kind": item_kind, "name": cleaned[:160], "bullets": [], "evidence": cleaned[:160]}
    flush()
    return experiences


def applied_experiences(jobs: list[dict]) -> list[dict]:
    found = []
    for job in jobs:
        title = str(job.get("title") or "").strip()
        if not title:
            continue
        description = str(job.get("description") or "")
        bullets = []
        for line in description.splitlines():
            piece = _bullet(line) or line.strip()
            if len(piece) < 8 or piece not in description:
                continue
            bullets.append(piece)
            if len(bullets) == 4:
                break
        if not bullets:
            bullets = [title]
        kind = "internship" if re.search(r"\bintern", title, re.I) else "job"
        found.append({"kind": kind, "name": title[:160], "bullets": bullets[:4], "evidence": title})
    return found


def all_experiences(resume_text: str, applied: list[dict]) -> list[dict]:
    return extract_experiences(resume_text) + applied_experiences(applied)


def build_source(resume_text: str, applied: list[dict]) -> str:
    parts = []
    if str(resume_text or "").strip():
        parts.append(str(resume_text).strip())
    for job in applied:
        title = str(job.get("title") or "").strip()
        company = str(job.get("company") or "").strip()
        if not title and not company:
            continue
        block = f"Applied: {title} at {company}".strip()
        description = str(job.get("description") or "").strip()
        if description:
            block = f"{block}\n{description}"
        parts.append(block)
    return "\n\n".join(parts)


def _slug(kind: str, name: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "-", name.casefold()).strip("-") or "item"
    return f"{kind}:{cleaned[:80]}"


def _add(items: list[dict], seen: set[str], *, kind: str, name: str, source: str, evidence: str, level: str = "", confidence: float = 0.55) -> None:
    if not quote_span(source, name):
        return
    quote = quote_span(source, evidence) or quote_span(source, name)
    if not quote or not name.strip():
        return
    key = _slug(kind, name)
    if key in seen:
        return
    seen.add(key)
    items.append(
        {
            "key": key,
            "kind": kind,
            "name": name.strip()[:160],
            "level": level,
            "confidence": confidence,
            "evidence": [quote],
            "status": "proposed",
            "locked": False,
            "weight": None,
            "source": "inferred",
            "document_backed": True,
            "writer": "document",
            "proposed_weight": 0.7,
            "polarity": "prefer",
            "experience_ref": "",
            "edited": False,
        }
    )


def document_items(source: str, experiences: list[dict]) -> list[dict]:
    """Items whose evidence is a verbatim slice of the source. No guessed facts."""
    items: list[dict] = []
    seen: set[str] = set()
    if not source.strip():
        return items
    parsed = parse_resume(source)
    for skill in parsed.get("skills") or []:
        _add(items, seen, kind="skill", name=str(skill), source=source, evidence=str(skill), level="listed")
    for title in parsed.get("titles") or []:
        _add(items, seen, kind="role_family", name=str(title), source=source, evidence=str(title))
    for experience in experiences:
        name = experience.get("name") or ""
        if experience.get("kind") in {"job", "internship"}:
            _add(items, seen, kind="role_family", name=name, source=source, evidence=experience.get("evidence") or name)
        elif experience.get("kind") == "project":
            _add(items, seen, kind="project", name=name, source=source, evidence=experience.get("evidence") or name)
        elif experience.get("kind") == "course":
            _add(items, seen, kind="degree", name=name, source=source, evidence=experience.get("evidence") or name)
    prose = "\n".join(line for line in source.splitlines() if not _header_kind(line.strip()))
    lowered = prose.casefold()
    for word in SENIORITY_WORDS:
        match = re.search(rf"(?<![a-z]){re.escape(word)}(?![a-z])", lowered)
        if not match:
            continue
        quote = prose[match.start() : match.end()]
        _add(items, seen, kind="seniority", name=quote, source=source, evidence=quote)
        break
    for word in INDUSTRY_WORDS:
        match = re.search(rf"(?<![a-z]){re.escape(word)}(?![a-z])", lowered)
        if not match:
            continue
        quote = prose[match.start() : match.end()]
        _add(items, seen, kind="industry", name=quote, source=source, evidence=quote, confidence=0.5)
    return items
