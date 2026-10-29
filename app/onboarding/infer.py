"""Model proposal for the profile. Invalid JSON is retried once, then the resume is quoted."""

from __future__ import annotations

import json
import re

from app.llm import ModelError, configured
from app.llm import _parse_json
from app.onboarding.documents import DOCUMENT_LABEL, MODEL_LABEL, document_items, quote_span
from app.onboarding.schema import schema_errors

SYSTEM = """You extract a career profile from documents the user already wrote.
Return one JSON object and nothing else, with this exact shape:
{
  "role_families": [{"name": "", "confidence": 0.0, "evidence": [""]}],
  "skills": [{"name": "", "level": "", "evidence": [""]}],
  "industries": [{"name": "", "confidence": 0.0, "evidence": [""]}],
  "seniority_guess": {"name": "", "confidence": 0.0, "evidence": [""]},
  "strengths": [{"name": "", "evidence": [""]}],
  "gaps": [{"name": "", "evidence": [""]}]
}
Rules:
- confidence is a number from 0 to 1.
- Every evidence string must be a short exact quote copied from the source.
- If you cannot quote an item from the source, omit that item.
- Do not invent employers, degrees, dates, skills, or job titles.
- seniority_guess is one object, not a string.
- Use an empty array when a list has no quoted items. seniority_guess still needs a quoted name; if you cannot quote one, the whole object is invalid.
"""


def _slug(kind: str, name: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "-", name.casefold()).strip("-") or "item"
    return f"{kind}:{cleaned[:80]}"


def _ground(evidence, source: str) -> list[str]:
    kept = []
    seen: set[str] = set()
    for quote in evidence or []:
        snippet = quote_span(source, str(quote))
        if not snippet:
            continue
        key = snippet.casefold()
        if key in seen:
            continue
        seen.add(key)
        kept.append(snippet)
    return kept


def ground_items(parsed: dict, source: str) -> list[dict]:
    """Drop any model claim whose evidence is not a quote from the source."""
    items = []
    seen: set[str] = set()

    def add(kind: str, name: str, evidence: list[str], *, level: str = "", confidence: float = 0.5) -> None:
        quotes = _ground(evidence, source)
        cleaned = " ".join(str(name).split())
        if not quotes or not cleaned or not quote_span(source, cleaned):
            return
        key = _slug(kind, cleaned)
        if key in seen:
            return
        seen.add(key)
        try:
            score = min(1.0, max(0.0, float(confidence)))
        except (TypeError, ValueError):
            score = 0.5
        items.append(
            {
                "key": key,
                "kind": kind,
                "name": cleaned[:160],
                "level": level,
                "confidence": round(score, 2),
                "evidence": quotes,
                "status": "proposed",
                "locked": False,
                "weight": None,
                "source": "inferred",
                "document_backed": False,
                "writer": "model",
                "proposed_weight": 0.7,
                "polarity": "prefer",
                "experience_ref": "",
                "edited": False,
            }
        )

    for entry in parsed.get("role_families") or []:
        add("role_family", entry.get("name"), entry.get("evidence"), confidence=entry.get("confidence"))
    for entry in parsed.get("skills") or []:
        add("skill", entry.get("name"), entry.get("evidence"), level=str(entry.get("level") or ""), confidence=0.6)
    for entry in parsed.get("industries") or []:
        add("industry", entry.get("name"), entry.get("evidence"), confidence=entry.get("confidence"))
    seniority = parsed.get("seniority_guess") or {}
    if isinstance(seniority, dict):
        add(
            "seniority",
            seniority.get("name"),
            seniority.get("evidence"),
            confidence=seniority.get("confidence"),
        )
    for entry in parsed.get("strengths") or []:
        add("strength", entry.get("name"), entry.get("evidence"), confidence=0.5)
    for entry in parsed.get("gaps") or []:
        add("gap", entry.get("name"), entry.get("evidence"), confidence=0.4)
    return items


def _messages(source: str) -> list[dict]:
    return [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": source[:12000]},
    ]


def _fallback(source: str, experiences: list[dict], attempts: int, reason: str) -> dict:
    return {
        "items": document_items(source, experiences),
        "writer": "document",
        "label": DOCUMENT_LABEL,
        "attempts": attempts,
        "fallback_reason": reason,
    }


async def infer_from_source(source: str, settings: dict, experiences: list[dict] | None = None, complete_fn=None) -> dict:
    """Ask the model once, retry once on invalid output, then quote the document."""
    experiences = experiences or []
    text = source or ""
    if not text.strip():
        return {
            "items": [],
            "writer": "document",
            "label": "No resume is stored yet.",
            "attempts": 0,
            "fallback_reason": "",
        }
    if not configured(settings or {}):
        return _fallback(text, experiences, 0, "no model configured")

    caller = complete_fn
    if caller is None:
        from app.llm import complete

        caller = complete
    messages = _messages(text)
    last_error = "The model did not return a usable profile."
    for attempt in (1, 2):
        try:
            raw = await caller(settings, messages, timeout=30)
        except (ModelError, Exception) as exc:
            last_error = str(exc) or "The model request failed."
            continue
        try:
            parsed = _parse_json(str(raw))
        except (json.JSONDecodeError, TypeError, ValueError):
            last_error = "The model did not return JSON."
            continue
        errors = schema_errors(parsed)
        if errors:
            last_error = errors[0]
            continue
        return {
            "items": ground_items(parsed, text),
            "writer": "model",
            "label": MODEL_LABEL,
            "attempts": attempt,
            "fallback_reason": "",
        }
    return _fallback(text, experiences, 2, last_error)
