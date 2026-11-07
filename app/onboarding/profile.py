"""Versioned preference profile, swipe updates, and the stable-action gate."""

from __future__ import annotations

CONFIRMED_WEIGHT = 0.7
REJECTED_WEIGHT = -0.7
SWIPE_STEP = 0.05
SWIPE_NEW = 0.15
MAJOR_WEIGHT = 0.3

STATUS_LABELS = {
    "not_started": "Not started",
    "constraints_done": "Constraints saved",
    "profile_confirmed": "Profile confirmed",
    "reflection_done": "Reflection saved",
    "complete": "Complete",
}


def sign(weight: float) -> int:
    if weight > 0:
        return 1
    if weight < 0:
        return -1
    return 0


def signature(fields: list[dict]) -> str:
    parts = []
    for field in fields:
        weight = float(field.get("weight") or 0)
        source = field.get("source") or ""
        if source != "constraint" and abs(weight) < MAJOR_WEIGHT:
            continue
        parts.append(
            "|".join(
                (
                    str(field.get("key") or ""),
                    str(field.get("name") or "").casefold(),
                    str(sign(weight)),
                    source,
                )
            )
        )
    return "\n".join(sorted(parts))


def is_major(previous: list[dict] | None, fields: list[dict]) -> bool:
    return signature(previous or []) != signature(fields)


def is_stable(versions: list[dict]) -> bool:
    """True after three recorded versions in a row with no major change."""
    if len(versions) < 3:
        return False
    return all(not version.get("major_change") for version in versions[-3:])


def _field_from_item(item: dict, *, weight: float, locked: bool) -> dict:
    return {
        "key": item["key"],
        "kind": item["kind"],
        "name": item["name"],
        "level": item.get("level") or "",
        "weight": weight,
        "confidence": item.get("confidence") if item.get("confidence") is not None else 0.5,
        "source": item.get("source") or "inferred",
        "evidence": list(item.get("evidence") or []),
        "locked": locked,
        "status": item.get("status") or "confirmed",
        "polarity": "prefer" if weight > 0 else "avoid",
        "document_backed": bool(item.get("document_backed")),
        "writer": item.get("writer") or "",
        "experience_ref": item.get("experience_ref") or "",
        "edited": bool(item.get("edited")),
        "updated_by": "",
    }


def decided_weight(item: dict, decision: str) -> tuple[float, bool]:
    proposed = float(item.get("proposed_weight") if item.get("proposed_weight") is not None else CONFIRMED_WEIGHT)
    if decision == "confirmed":
        weight = CONFIRMED_WEIGHT if proposed >= 0 else REJECTED_WEIGHT
        return weight, True
    return REJECTED_WEIGHT, False


def fields_from_items(items: list[dict]) -> list[dict]:
    """Confirmed lines lock at 0.7. Rejected lines get a negative weight. Proposals stay out."""
    fields = []
    for item in items:
        status = item.get("status")
        if status == "confirmed":
            weight, locked = decided_weight(item, "confirmed")
            fields.append(_field_from_item(item, weight=weight, locked=locked))
        elif status == "rejected":
            fields.append(_field_from_item(item, weight=REJECTED_WEIGHT, locked=False))
    return fields


def _constraint_field(key: str, kind: str, name: str, *, polarity: str = "prefer") -> dict:
    weight = 1.0 if polarity == "prefer" else -1.0
    return {
        "key": key,
        "kind": kind,
        "name": name,
        "level": "",
        "weight": weight,
        "confidence": 1.0,
        "source": "constraint",
        "evidence": [],
        "locked": False,
        "status": "confirmed",
        "polarity": polarity,
        "document_backed": False,
        "writer": "user",
        "experience_ref": "",
        "edited": False,
        "updated_by": "",
    }


def constraint_fields(filters: dict) -> list[dict]:
    fields = []
    if filters.get("locations"):
        fields.append(_constraint_field("constraint:locations", "location", ", ".join(filters["locations"])))
    if filters.get("willing_to_relocate"):
        fields.append(
            _constraint_field(
                "constraint:relocate",
                "relocate",
                f"Willing to relocate: {filters['willing_to_relocate']}",
            )
        )
    remote = filters.get("remote_preference") or ""
    if remote and remote != "no_preference":
        fields.append(_constraint_field("constraint:remote", "remote", f"Work mode: {remote}"))
    if filters.get("employment_types"):
        fields.append(
            _constraint_field(
                "constraint:employment",
                "employment",
                ", ".join(filters["employment_types"]),
            )
        )
    if filters.get("weekly_hours") is not None:
        fields.append(
            _constraint_field("constraint:hours", "hours", f"{filters['weekly_hours']:g} hours a week")
        )
    if filters.get("min_salary") is not None:
        flexible = " flexible" if filters.get("salary_flexible") else ""
        label = f"{filters['min_salary']:g} {filters.get('salary_currency') or ''}{flexible}".strip()
        fields.append(_constraint_field("constraint:salary", "salary", label))
    for entry in filters.get("languages") or []:
        name = entry["name"]
        key = "constraint:language:" + _slug(name)
        fields.append(_constraint_field(key, "language", f"{name} ({entry['level']})"))
    for entry in filters.get("work_authorization") or []:
        key = "constraint:auth:" + _slug(entry["country"])
        note = entry["notes"] or "noted"
        fields.append(_constraint_field(key, "authorization", f"{entry['country']}: {note}"))
    for name in filters.get("exclude_industries") or []:
        fields.append(
            _constraint_field("constraint:industry:" + _slug(name), "exclude_industry", name, polarity="avoid")
        )
    for name in filters.get("exclude_companies") or []:
        fields.append(
            _constraint_field("constraint:company:" + _slug(name), "exclude_company", name, polarity="avoid")
        )
    if filters.get("earliest_start"):
        fields.append(_constraint_field("constraint:start", "start", f"Start on or after {filters['earliest_start']}"))
    return fields


def _slug(name: str) -> str:
    import re

    return re.sub(r"[^a-z0-9]+", "-", name.casefold()).strip("-")[:80] or "item"


def assemble_fields(items: list[dict], filters: dict, previous: list[dict] | None = None) -> list[dict]:
    fields = fields_from_items(items) + constraint_fields(filters)
    keys = {field["key"] for field in fields}
    for field in previous or []:
        if field.get("source") == "swipe" and field.get("key") not in keys:
            fields.append(dict(field))
    return fields


def posting_signals(posting: dict) -> list[dict]:
    title = str(posting.get("title") or "").strip()
    description = str(posting.get("description") or "").strip()
    evidence = title or description or str(posting.get("role_family") or "").strip()
    signals = []
    family = str(posting.get("role_family") or "").strip()
    if family:
        signals.append({"kind": "role_family", "name": family, "evidence": evidence or family})
    for skill in posting.get("skills") or []:
        name = str(skill).strip()
        if name:
            signals.append({"kind": "skill", "name": name, "evidence": evidence or name})
    industry = str(posting.get("industry") or "").strip()
    if industry:
        signals.append({"kind": "industry", "name": industry, "evidence": evidence or industry})
    if not signals and title:
        signals.append({"kind": "role_family", "name": title, "evidence": title})
    return signals


def _swipe_weight(current: float | None, verdict: str, *, created: bool) -> float:
    if created:
        base = {"yes": SWIPE_NEW, "no": -SWIPE_NEW, "maybe": 0.0}[verdict]
        return base
    weight = float(current or 0)
    if verdict == "yes":
        weight += SWIPE_STEP
    elif verdict == "no":
        weight -= SWIPE_STEP
    return round(max(-1.0, min(1.0, weight)), 2)


def apply_swipe(fields: list[dict], verdict: str, posting: dict) -> tuple[list[dict], list[dict]]:
    if verdict not in {"yes", "no", "maybe"}:
        raise ValueError("Choose yes, maybe, or no.")
    signals = posting_signals(posting)
    if not signals:
        raise ValueError("That swipe did not name a role, skill, or industry.")
    updated = [dict(field) for field in fields]
    by_key = {field["key"]: field for field in updated}
    for signal in signals:
        key = f"{signal['kind']}:{_slug(signal['name'])}"
        existing = by_key.get(key)
        if existing and existing.get("locked"):
            continue
        if existing is None:
            weight = _swipe_weight(None, verdict, created=True)
            field = {
                "key": key,
                "kind": signal["kind"],
                "name": signal["name"][:160],
                "level": "",
                "weight": weight,
                "confidence": 0.35 if verdict != "maybe" else 0.2,
                "source": "swipe",
                "evidence": [signal["evidence"][:240]],
                "locked": False,
                "status": "confirmed",
                "polarity": "prefer" if weight >= 0 else "avoid",
                "document_backed": False,
                "writer": "swipe",
                "experience_ref": "",
                "edited": False,
                "updated_by": "swipe",
            }
            updated.append(field)
            by_key[key] = field
            continue
        existing["weight"] = _swipe_weight(existing.get("weight"), verdict, created=False)
        existing["polarity"] = "prefer" if existing["weight"] >= 0 else "avoid"
        existing["updated_by"] = "swipe"
        if existing.get("confidence") is not None and verdict == "maybe":
            existing["confidence"] = round(max(0.1, float(existing["confidence"]) - 0.02), 2)
    return updated, signals


def gate_decision(stable: bool, override: str) -> dict:
    """Actions stay off until the profile is stable, unless the user overrides the flag."""
    mode = override if override in {"allow", "deny"} else ""
    if mode == "allow":
        allowed = True
        reason = "override"
    elif mode == "deny":
        allowed = False
        reason = "flag_off"
    elif stable:
        allowed = True
        reason = ""
    else:
        allowed = False
        reason = "not_stable"
    if reason == "not_stable":
        detail = "Matching, resume tailoring, and auto-prepared applications stay off until the preference profile is stable."
    elif reason == "flag_off":
        detail = "Those actions are turned off."
    else:
        detail = "The action is unlocked. Onboarding does not run matching, resume tailoring, or auto-apply."
    return {
        "stable": stable,
        "override": mode,
        "entry_allowed": allowed,
        "match": allowed,
        "resume_tailor": allowed,
        "auto_prepare": allowed,
        "reason": reason,
        "detail": detail,
        "performed": False,
    }
