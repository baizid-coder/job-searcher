"""Preference summaries from verdicts, with a count-based fallback when no model runs."""

from __future__ import annotations

import json

from app.discover.classify import FAMILY_LABELS, SUMMARY_EVERY
from app.llm import ModelError, complete, configured

FALLBACK_LABEL = "Computed from verdicts, not from a model."
MODEL_LABEL = "Summarized by the configured model."
MIN_SUPPORT = 3
DIMENSIONS = ("role_family", "industry", "seniority", "skill", "location", "pay", "dislike")
DIMENSION_ORDER = {name: index for index, name in enumerate(DIMENSIONS)}

# A family is low-confidence when the stronger side is under this share, or the votes split.
LOW_CONFIDENCE = 0.65


def empty_counts() -> dict:
    return {"yes": 0, "no": 0, "maybe": 0}


def bump(bucket: dict, verdict: str) -> None:
    if verdict in bucket:
        bucket[verdict] += 1


def confidence(counts: dict, polarity: str) -> float:
    total = counts["yes"] + counts["no"] + counts["maybe"]
    if total <= 0:
        return 0.0
    focus = counts["yes"] if polarity == "prefer" else counts["no"]
    return round(focus / total, 2)


def polarity_for(counts: dict) -> str | None:
    if counts["yes"] >= MIN_SUPPORT and counts["yes"] > counts["no"]:
        return "prefer"
    if counts["no"] >= MIN_SUPPORT and counts["no"] > counts["yes"]:
        return "dislike"
    return None


def signal_kind(counts: dict) -> str:
    """How much a new posting from this bucket would teach us."""
    total = counts["yes"] + counts["no"] + counts["maybe"]
    if total == 0:
        return "unseen"
    stronger = max(counts["yes"], counts["no"]) / total
    split = (
        counts["yes"] >= 2
        and counts["no"] >= 2
        and min(counts["yes"], counts["no"]) >= 0.3 * (counts["yes"] + counts["no"])
    )
    if split or stronger < LOW_CONFIDENCE:
        return "explore"
    return "known"


def aggregate(pairs: list[dict]) -> dict:
    families: dict[str, dict] = {}
    industries: dict[str, dict] = {}
    seniorities: dict[str, dict] = {}
    skills: dict[str, dict] = {}
    locations: dict[str, dict] = {}
    chips: dict[str, dict] = {}
    pay_listed = empty_counts()

    def slot(table: dict, key: str) -> dict:
        table.setdefault(key, empty_counts())
        return table[key]

    for pair in pairs:
        verdict = pair["verdict"]["verdict"]
        posting = pair["posting"]
        family = posting.get("role_family") or "other"
        if family != "other":
            bump(slot(families, family), verdict)
        if posting.get("industry"):
            bump(slot(industries, posting["industry"]), verdict)
        if posting.get("seniority") not in {"", "unknown", None}:
            bump(slot(seniorities, posting["seniority"]), verdict)
        for skill in posting.get("skills") or []:
            bump(slot(skills, skill), verdict)
        if posting.get("is_remote"):
            bump(slot(locations, "remote"), verdict)
        if posting.get("city"):
            bump(slot(locations, posting["city"]), verdict)
        if posting.get("salary"):
            bump(pay_listed, verdict)
        for chip in pair["verdict"].get("reason_chips") or []:
            bump(slot(chips, chip), verdict)
    return {
        "families": families,
        "industries": industries,
        "seniorities": seniorities,
        "skills": skills,
        "locations": locations,
        "chips": chips,
        "pay_listed": pay_listed,
    }


def _line(dimension: str, polarity: str, value: str, counts: dict, text: str) -> dict:
    return {
        "key": f"{dimension}:{value}",
        "dimension": dimension,
        "polarity": polarity,
        "value": value,
        "text": text,
        "confidence": confidence(counts, polarity),
        "counts": {"yes": counts["yes"], "no": counts["no"], "maybe": counts["maybe"]},
        "status": "open",
        "locked": False,
    }


def _ranked(table: dict, polarity: str, limit: int) -> list[tuple[str, dict]]:
    chosen = [(key, counts) for key, counts in table.items() if polarity_for(counts) == polarity]
    focus = "yes" if polarity == "prefer" else "no"
    chosen.sort(key=lambda item: (-item[1][focus], item[0]))
    return chosen[:limit]


def _dislike_text(value: str) -> str:
    kind, _, rest = value.partition(":")
    if kind == "role":
        return f"You tend to pass on {FAMILY_LABELS.get(rest, rest)} roles."
    if kind == "industry":
        return f"You tend to pass on {rest} work."
    if kind == "seniority":
        return f"You tend to pass on {rest} roles."
    if kind == "skill":
        return f"You tend to pass when a role asks for {rest}."
    if kind == "location":
        return f"You tend to pass on roles in {rest.title()}."
    if kind == "chip":
        label = {
            "salary": "Pay",
            "role_type": "Role type",
            "company": "The company",
            "location": "Location",
            "seniority": "Seniority",
            "too_technical": "Being too technical",
            "not_technical_enough": "Not being technical enough",
            "industry": "Industry",
        }.get(rest, rest)
        return f"{label} is a reason you pass on roles."
    return f"You tend to pass on {value}."


def fallback_lines(agg: dict) -> list[dict]:
    lines: list[dict] = []
    for family, counts in _ranked(agg["families"], "prefer", 6):
        label = FAMILY_LABELS.get(family, family)
        lines.append(_line("role_family", "prefer", family, counts, f"You tend to want {label} roles."))
    for industry, counts in _ranked(agg["industries"], "prefer", 4):
        lines.append(_line("industry", "prefer", industry, counts, f"You tend to want {industry} work."))
    for level, counts in _ranked(agg["seniorities"], "prefer", 3):
        lines.append(_line("seniority", "prefer", level, counts, f"You tend to want {level} roles."))
    for skill, counts in _ranked(agg["skills"], "prefer", 6):
        lines.append(_line("skill", "prefer", skill, counts, f"You tend to want roles that ask for {skill}."))
    for place, counts in _ranked(agg["locations"], "prefer", 4):
        if place == "remote":
            text = "You lean toward remote roles."
        else:
            text = f"You lean toward roles in {place.title()}."
        lines.append(_line("location", "prefer", place, counts, text))
    pay = agg["pay_listed"]
    if polarity_for(pay) == "prefer":
        lines.append(_line("pay", "prefer", "listed", pay, "You tend to want roles that list pay."))

    dislikes: list[tuple[str, dict, str]] = []
    for family, counts in _ranked(agg["families"], "dislike", 6):
        dislikes.append((f"role:{family}", counts, _dislike_text(f"role:{family}")))
    for industry, counts in _ranked(agg["industries"], "dislike", 4):
        dislikes.append((f"industry:{industry}", counts, _dislike_text(f"industry:{industry}")))
    for level, counts in _ranked(agg["seniorities"], "dislike", 3):
        dislikes.append((f"seniority:{level}", counts, _dislike_text(f"seniority:{level}")))
    for skill, counts in _ranked(agg["skills"], "dislike", 4):
        dislikes.append((f"skill:{skill}", counts, _dislike_text(f"skill:{skill}")))
    for place, counts in _ranked(agg["locations"], "dislike", 4):
        if place == "remote":
            continue
        dislikes.append((f"location:{place}", counts, _dislike_text(f"location:{place}")))
    for chip, counts in _ranked(agg["chips"], "dislike", 6):
        dislikes.append((f"chip:{chip}", counts, _dislike_text(f"chip:{chip}")))
    if polarity_for(pay) == "dislike":
        dislikes.append(("pay:listed", pay, "Pay is a reason you pass on roles."))
    for value, counts, text in dislikes:
        lines.append(_line("dislike", "dislike", value, counts, text))

    lines.sort(key=lambda line: (DIMENSION_ORDER.get(line["dimension"], 99), line["key"]))
    return lines


def apply_decisions(lines: list[dict], decisions: dict[str, dict]) -> list[dict]:
    visible: list[dict] = []
    for line in lines:
        decision = decisions.get(line["key"])
        if decision and decision["status"] == "rejected":
            continue
        merged = dict(line)
        if decision and decision["status"] == "confirmed":
            merged["status"] = "confirmed"
            merged["locked"] = bool(decision["locked"])
            if decision.get("locked") and decision.get("text"):
                merged["text"] = decision["text"]
            elif decision.get("edited") and decision.get("text"):
                merged["text"] = decision["text"]
        else:
            merged["status"] = "open"
            merged["locked"] = False
        visible.append(merged)
    return visible


def signature(lines: list[dict]) -> tuple:
    """Confirmed role families, location/remote, and dislikes. Used to spot a major change."""
    confirmed = [line for line in lines if line.get("status") == "confirmed"]

    def values(dimension: str) -> tuple:
        return tuple(sorted(line["value"] for line in confirmed if line.get("dimension") == dimension))

    return (values("role_family"), values("location"), values("dislike"))


def most_confirmed(lines: list[dict]) -> bool:
    visible = [line for line in lines if line.get("status") != "rejected"]
    if not visible:
        return False
    confirmed = [line for line in visible if line.get("status") == "confirmed"]
    return len(confirmed) * 2 > len(visible)


def is_stable(summaries: list[dict]) -> bool:
    if len(summaries) < 3:
        return False
    last = summaries[-3:]
    signs = [signature(item["lines"]) for item in last]
    if signs[0] != signs[1] or signs[1] != signs[2]:
        return False
    return most_confirmed(last[-1]["lines"])


def _model_messages(agg: dict) -> list[dict]:
    system = (
        "You summarize reactions to job postings. Use only the counts in the user message. "
        "Return a JSON object with a lines array. Each line has dimension "
        "(role_family, industry, seniority, skill, location, pay, or dislike), "
        "polarity (prefer or dislike), value, text (one plain sentence), "
        "confidence from 0 to 1, and counts with yes, no, and maybe integers. "
        "Do not invent employers, salaries, or postings."
    )
    compact = {key: value for key, value in agg.items()}
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps(compact)},
    ]


def _parse_model_lines(text: str) -> list[dict] | None:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        cleaned = cleaned.removeprefix("json").strip()
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            parsed = json.loads(cleaned[start : end + 1])
        except json.JSONDecodeError:
            return None
    raw_lines = parsed.get("lines") if isinstance(parsed, dict) else None
    if not isinstance(raw_lines, list) or not raw_lines:
        return None
    lines: list[dict] = []
    for item in raw_lines:
        if not isinstance(item, dict):
            return None
        dimension = str(item.get("dimension") or "")
        polarity = str(item.get("polarity") or "")
        value = str(item.get("value") or "").strip()
        sentence = str(item.get("text") or "").strip()
        counts = item.get("counts") if isinstance(item.get("counts"), dict) else None
        if dimension not in DIMENSIONS or polarity not in {"prefer", "dislike"} or not value or not sentence or not counts:
            return None
        try:
            normalized = {
                "yes": int(counts.get("yes") or 0),
                "no": int(counts.get("no") or 0),
                "maybe": int(counts.get("maybe") or 0),
            }
            score = float(item.get("confidence"))
        except (TypeError, ValueError):
            return None
        score = max(0.0, min(1.0, score))
        lines.append(
            {
                "key": f"{dimension}:{value}",
                "dimension": dimension,
                "polarity": polarity,
                "value": value,
                "text": sentence,
                "confidence": round(score, 2),
                "counts": normalized,
                "status": "open",
                "locked": False,
            }
        )
    return lines


async def build_lines(pairs: list[dict], settings: dict) -> dict:
    """Use the configured model when it works. Otherwise count the verdicts."""
    agg = aggregate(pairs)
    fallback = {"source": "verdicts", "label": FALLBACK_LABEL, "lines": fallback_lines(agg)}
    if not configured(settings):
        return fallback
    try:
        text = await complete(settings, _model_messages(agg), timeout=20)
        lines = _parse_model_lines(text)
    except (ModelError, TimeoutError, json.JSONDecodeError, TypeError, ValueError):
        return fallback
    except Exception:
        return fallback
    if not lines:
        return fallback
    return {"source": "model", "label": MODEL_LABEL, "lines": lines}


def next_summary_in(verdict_count: int) -> int:
    remainder = verdict_count % SUMMARY_EVERY
    if remainder == 0:
        return SUMMARY_EVERY
    return SUMMARY_EVERY - remainder
