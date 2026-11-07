"""Hard filters. A set constraint removes jobs that fail it. Empty constraints remove nothing."""

from __future__ import annotations

import re
from datetime import datetime

CURRENCIES = {"USD", "BDT", "EUR", "GBP", "JPY", "CAD", "AUD", "INR"}
REMOTE_PREFS = {"", "remote", "hybrid", "onsite", "no_preference"}
RELOCATE = {"", "yes", "no", "maybe"}
EMPLOYMENT = ("full-time", "part-time", "internship", "contract")
LEVELS = ("none", "basic", "conversational", "fluent")
LEVEL_RANK = {name: index for index, name in enumerate(LEVELS)}

EMPLOYMENT_PATTERNS = {
    "full-time": re.compile(r"full[\s-]?time|permanent role", re.I),
    "part-time": re.compile(r"part[\s-]?time", re.I),
    "internship": re.compile(r"\bintern(?:ship)?\b", re.I),
    "contract": re.compile(r"\bcontract(?:or|ing)?\b|\bfreelance\b", re.I),
}

_NEGATIVE_AUTH = re.compile(
    r"not authorized|no authorization|cannot work|can't work|no visa|without a visa|without visa",
    re.I,
)
_RELOCATION_REQUIRED = re.compile(r"relocation required|must relocate|relocation is required", re.I)
_HOURS = re.compile(r"\b(\d{1,2})\s+hours?\s+per\s+week\b", re.I)
_START = re.compile(r"start(?:ing)?(?: date)?:\s*(\d{4}-\d{2}-\d{2})", re.I)


def empty_constraints() -> dict:
    return normalize_constraints({})


def _terms(values, *, limit: int = 40) -> list[str]:
    if values is None:
        return []
    if isinstance(values, str):
        raw_items = re.split(r"[\n,]", values)
    elif isinstance(values, list):
        raw_items = []
        for item in values:
            raw_items.extend(re.split(r"[\n,]", str(item)))
    else:
        raw_items = [str(values)]
    terms: list[str] = []
    seen: set[str] = set()
    for item in raw_items:
        term = " ".join(str(item).split()).strip()
        if not term or len(term) > 80:
            continue
        key = term.casefold()
        if key in seen:
            continue
        seen.add(key)
        terms.append(term)
        if len(terms) >= limit:
            break
    return terms


def _number(value) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise ValueError("Enter a number.")
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("Enter a number.") from exc


def _remote(value: str) -> str:
    text = str(value or "").strip().casefold().replace("-", " ")
    text = text.replace(" ", "_")
    if text in {"nopreference", "any", "none"}:
        return "no_preference"
    if text not in REMOTE_PREFS:
        raise ValueError("Choose remote, hybrid, onsite, or no preference.")
    return text


def _relocate(value: str) -> str:
    text = str(value or "").strip().casefold()
    if text not in RELOCATE:
        raise ValueError("Choose yes, no, or maybe for relocation.")
    return text


def normalize_constraints(raw: dict | None) -> dict:
    data = raw or {}
    weekly = _number(data.get("weekly_hours"))
    if weekly is not None and not 0 <= weekly <= 168:
        raise ValueError("Weekly hours need to be between 0 and 168.")
    salary = _number(data.get("min_salary"))
    if salary is not None and salary < 0:
        raise ValueError("Minimum salary cannot be negative.")
    earliest = str(data.get("earliest_start") or "").strip()
    if earliest:
        try:
            datetime.strptime(earliest, "%Y-%m-%d")
        except ValueError as exc:
            raise ValueError("Earliest start needs a real date.") from exc
    currency = str(data.get("salary_currency") or "").strip().upper()
    if currency and currency not in CURRENCIES:
        raise ValueError("Choose a supported currency.")
    if salary is not None and not currency:
        currency = "BDT"
    languages = []
    for entry in data.get("languages") or []:
        if isinstance(entry, str):
            name, level = entry, ""
        else:
            name = str((entry or {}).get("name") or "").strip()
            level = str((entry or {}).get("level") or "").strip().casefold()
        if not name:
            continue
        if level not in LEVELS:
            raise ValueError("Choose a language level: none, basic, conversational, or fluent.")
        languages.append({"name": name[:80], "level": level})
        if len(languages) >= 40:
            break
    authorizations = []
    for entry in data.get("work_authorization") or []:
        if not isinstance(entry, dict):
            continue
        country = " ".join(str(entry.get("country") or "").split())
        notes = str(entry.get("notes") or "").strip()
        if not country and not notes:
            continue
        if not country:
            raise ValueError("Add a country for each work authorization note.")
        authorizations.append({"country": country[:80], "notes": notes[:500]})
        if len(authorizations) >= 40:
            break
    employment = []
    for item in _terms(data.get("employment_types")):
        lowered = item.casefold()
        if lowered in EMPLOYMENT and lowered not in employment:
            employment.append(lowered)
    return {
        "locations": _terms(data.get("locations")),
        "willing_to_relocate": _relocate(data.get("willing_to_relocate") or ""),
        "remote_preference": _remote(data.get("remote_preference") or ""),
        "employment_types": employment,
        "weekly_hours": weekly,
        "min_salary": salary,
        "salary_currency": currency,
        "salary_flexible": bool(data.get("salary_flexible")),
        "languages": languages,
        "work_authorization": authorizations,
        "exclude_industries": _terms(data.get("exclude_industries")),
        "exclude_companies": _terms(data.get("exclude_companies")),
        "earliest_start": earliest,
    }


def constraints_active(filters: dict) -> bool:
    data = filters or {}
    if data.get("locations") or data.get("employment_types"):
        return True
    if data.get("exclude_industries") or data.get("exclude_companies") or data.get("languages"):
        return True
    if data.get("work_authorization") or data.get("earliest_start"):
        return True
    if data.get("weekly_hours") is not None or data.get("min_salary") is not None:
        return True
    if data.get("willing_to_relocate") or data.get("remote_preference") not in {"", "no_preference", None}:
        return True
    return False


def _blob(*parts) -> str:
    return " ".join(str(part or "") for part in parts).casefold()


def _location_blob(job: dict) -> str:
    return _blob(job.get("location"), job.get("location_note"), job.get("title"))


def _description(job: dict) -> str:
    return _blob(job.get("description"), job.get("posting_text"), job.get("summary"))


def job_is_remote(job: dict) -> bool:
    if job.get("is_remote") is True:
        return True
    return "remote" in _blob(_location_blob(job), _description(job), job.get("work_mode"))


def job_is_hybrid(job: dict) -> bool:
    return "hybrid" in _blob(_location_blob(job), _description(job), job.get("work_mode"))


def _location_fails(job: dict, filters: dict) -> bool:
    locations = filters.get("locations") or []
    if not locations or filters.get("willing_to_relocate") == "yes":
        return False
    blob = _location_blob(job)
    return not any(place.casefold() in blob for place in locations)


def _relocate_fails(job: dict, filters: dict) -> bool:
    if filters.get("willing_to_relocate") != "no":
        return False
    return bool(_RELOCATION_REQUIRED.search(_blob(_location_blob(job), _description(job))))


def _remote_fails(job: dict, filters: dict) -> bool:
    pref = filters.get("remote_preference") or ""
    if pref in {"", "no_preference"}:
        return False
    remote = job_is_remote(job)
    hybrid = job_is_hybrid(job)
    if pref == "remote":
        return not remote and not hybrid
    if pref == "onsite":
        return remote and not hybrid
    if pref == "hybrid":
        if re.search(r"on-?site only|in-office only|no remote", _description(job)):
            return True
        return remote and not hybrid
    return False


def _detected_employment(job: dict) -> set[str]:
    explicit = str(job.get("employment_type") or "").strip().casefold()
    found = set()
    if explicit in EMPLOYMENT:
        found.add(explicit)
    blob = _blob(job.get("title"), _description(job))
    for name, pattern in EMPLOYMENT_PATTERNS.items():
        if pattern.search(blob):
            found.add(name)
    return found


def _employment_fails(job: dict, filters: dict) -> bool:
    allowed = set(filters.get("employment_types") or [])
    if not allowed:
        return False
    found = _detected_employment(job)
    if not found:
        return False
    return found.isdisjoint(allowed)


def _hours_fails(job: dict, filters: dict) -> bool:
    limit = filters.get("weekly_hours")
    if limit is None:
        return False
    hours = job.get("weekly_hours")
    if hours is None:
        match = _HOURS.search(_description(job))
        hours = float(match.group(1)) if match else None
    if hours is None:
        return False
    try:
        return float(hours) > float(limit)
    except (TypeError, ValueError):
        return False


def parse_salary(job: dict) -> tuple[float | None, str]:
    raw = job.get("salary")
    currency = str(job.get("salary_currency") or "").strip().upper()
    if isinstance(raw, bool):
        return None, currency
    if isinstance(raw, (int, float)):
        return float(raw), currency
    text = str(raw or "")
    numbers = re.findall(r"\d[\d,]*(?:\.\d+)?", text)
    if not numbers:
        return None, currency
    amount = max(float(number.replace(",", "")) for number in numbers)
    upper = text.upper()
    if not currency:
        for code in CURRENCIES:
            if code in upper:
                currency = code
                break
    if not currency and "$" in text:
        currency = "USD"
    if not currency and ("৳" in text or "BDT" in upper or "TAKA" in upper):
        currency = "BDT"
    return amount, currency


def _salary_fails(job: dict, filters: dict) -> bool:
    floor = filters.get("min_salary")
    if floor is None or filters.get("salary_flexible"):
        return False
    amount, currency = parse_salary(job)
    if amount is None:
        return False
    want = filters.get("salary_currency") or ""
    if currency and want and currency != want:
        return False
    return amount < float(floor)


def _required_languages(job: dict) -> list[dict]:
    given = job.get("required_languages") or []
    found = []
    for entry in given:
        if isinstance(entry, dict) and entry.get("name") and entry.get("level") in LEVEL_RANK:
            found.append({"name": str(entry["name"]), "level": entry["level"]})
    if found:
        return found
    description = str(job.get("description") or "")
    for match in re.finditer(r"\b(fluent|conversational|basic)\s+([A-Za-z][A-Za-z .'-]{1,40})", description, re.I):
        found.append({"name": match.group(2).strip(), "level": match.group(1).casefold()})
    return found


def _language_fails(job: dict, filters: dict) -> bool:
    stated = {item["name"].casefold(): item["level"] for item in filters.get("languages") or []}
    if not stated:
        return False
    for required in _required_languages(job):
        name = required["name"].casefold()
        if name not in stated:
            continue
        if LEVEL_RANK[stated[name]] < LEVEL_RANK[required["level"]]:
            return True
    return False


def _auth_fails(job: dict, filters: dict) -> bool:
    notes = filters.get("work_authorization") or []
    if not notes:
        return False
    blob = _blob(job.get("location"), job.get("location_note"), _description(job))
    for entry in notes:
        country = str(entry.get("country") or "").casefold()
        text = str(entry.get("notes") or "")
        if not country or not text or country not in blob:
            continue
        if _NEGATIVE_AUTH.search(text):
            return True
    return False


def _industry_fails(job: dict, filters: dict) -> bool:
    blocked = filters.get("exclude_industries") or []
    if not blocked:
        return False
    blob = _blob(job.get("industry"), job.get("title"), _description(job))
    return any(name.casefold() in blob for name in blocked)


def _company_fails(job: dict, filters: dict) -> bool:
    blocked = filters.get("exclude_companies") or []
    if not blocked:
        return False
    company = _blob(job.get("company"))
    return any(name.casefold() in company for name in blocked)


def _start_fails(job: dict, filters: dict) -> bool:
    earliest = filters.get("earliest_start") or ""
    if not earliest:
        return False
    start = str(job.get("start_date") or "").strip()
    if not start:
        match = _START.search(str(job.get("description") or ""))
        start = match.group(1) if match else ""
    if not start:
        return False
    return start < earliest


def passes_constraints(job: dict, constraints: dict | None) -> bool:
    """True when the job survives every constraint the user actually set."""
    try:
        filters = normalize_constraints(constraints or {})
    except ValueError:
        filters = constraints or {}
    if _location_fails(job, filters):
        return False
    if _relocate_fails(job, filters):
        return False
    if _remote_fails(job, filters):
        return False
    if _employment_fails(job, filters):
        return False
    if _hours_fails(job, filters):
        return False
    if _salary_fails(job, filters):
        return False
    if _language_fails(job, filters):
        return False
    if _auth_fails(job, filters):
        return False
    if _industry_fails(job, filters):
        return False
    if _company_fails(job, filters):
        return False
    if _start_fails(job, filters):
        return False
    return True
