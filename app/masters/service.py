"""Rules for the master's tracker.

Drafts stay drafts until the person approves them. Approval does not send mail
and does not mark an application submitted.
"""

from __future__ import annotations

import json
import os
import re
import uuid
from datetime import date, datetime, timedelta, timezone
from html import unescape
from pathlib import Path
from urllib.parse import urlparse

import httpx

from app import db
from app.llm import ModelError, complete, configured
from app.masters import store
from app.textutil import html_to_text, is_public_http

REMINDER_OFFSETS = (30, 14, 7, 3, 1)
STALE_AFTER = timedelta(days=90)
MAX_UPLOAD_BYTES = 8 * 1024 * 1024
WORDS_PER_PAGE = 350
ACTOR = "you"
FETCH_USER_AGENT = "JobFinder/1.0 (personal master's application tracker; public pages only)"

PROGRAM_STATUSES = (
    "shortlisted",
    "preparing",
    "ready_for_review",
    "submitted",
    "admitted",
    "rejected",
    "waitlisted",
    "withdrawn",
)
OUTCOMES = {"admitted", "rejected", "waitlisted", "withdrawn"}
CHECKLIST_KINDS = {
    "transcript",
    "cv",
    "sop",
    "research_proposal",
    "lor",
    "test_score",
    "passport",
    "photo",
    "fee_paid",
    "custom",
}
CHECKLIST_STATUSES = {"todo", "in_progress", "done"}
DEADLINE_TYPES = {"application", "documents", "recommender", "scholarship", "test_score"}
DOC_TYPES = {"transcript", "cv", "passport", "certificate", "photo", "sop", "research_proposal", "other"}
TEST_TYPES = {"IELTS", "TOEFL", "GRE", "JLPT"}
WRITING_KINDS = {"sop", "research_proposal"}
PROFESSOR_STATUSES = {"identified", "emailed", "replied", "meeting", "no_response"}
RECOMMENDER_STATUSES = {"not_asked", "asked", "submitted"}
JLPT_RANK = {"N1": 5, "N2": 4, "N3": 3, "N4": 2, "N5": 1}

PROGRAM_TEXT = (
    "university",
    "department",
    "degree",
    "country",
    "intake",
    "application_url",
    "portal_url",
    "language_requirement",
    "notes",
    "source_url",
    "research_areas",
    "requirements_text",
)


class MastersError(Exception):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


def init() -> None:
    store.init()


def _audit(action: str, subject_type: str, subject_id, detail: str = "") -> None:
    store.add_audit(ACTOR, action, subject_type, subject_id, detail)


def _optional_float(value, label: str):
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise MastersError(f"{label} must be a number.") from exc


def _optional_int(value, label: str):
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise MastersError(f"{label} must be a whole number.") from exc


def _required_float(value, label: str) -> float:
    if value is None or value == "":
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise MastersError(f"{label} must be a number.") from exc


def _date_text(value, label: str) -> str | None:
    if value is None or str(value).strip() == "":
        return None
    text = str(value).strip()[:10]
    try:
        date.fromisoformat(text)
    except ValueError as exc:
        raise MastersError(f"{label} must be a YYYY-MM-DD date.") from exc
    return text


def word_count(text: str) -> int:
    return len(re.findall(r"\S+", text or ""))


def page_estimate(text: str) -> int:
    words = word_count(text)
    if words == 0:
        return 0
    return (words + WORDS_PER_PAGE - 1) // WORDS_PER_PAGE


def deadline_band(day: date, today: date | None = None) -> tuple[int, str]:
    today = today or date.today()
    days = (day - today).days
    if days < 0:
        return days, "overdue"
    if days <= 3:
        return days, "d3"
    if days <= 7:
        return days, "d7"
    if days <= 14:
        return days, "d14"
    return days, "later"


def is_stale(program: dict, now: datetime | None = None) -> bool:
    if not program.get("verified") or not program.get("verified_at"):
        return False
    moment = now or datetime.now(timezone.utc)
    verified = datetime.fromisoformat(program["verified_at"])
    if verified.tzinfo is None:
        verified = verified.replace(tzinfo=timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment - verified > STALE_AFTER


def stale_message(program: dict, now: datetime | None = None) -> str:
    if not is_stale(program, now):
        return ""
    return (
        f"Last verified {program['verified_at'][:10]}, more than 90 days ago. "
        "Check the source page again before you rely on these fields."
    )


def parse_language_requirement(text: str) -> dict | None:
    raw = (text or "").strip()
    if not raw:
        return None
    match = re.search(r"IELTS[^0-9]{0,16}(\d+(?:\.\d+)?)", raw, re.I)
    if match:
        return {"test": "IELTS", "minimum": float(match.group(1)), "raw": raw}
    match = re.search(r"TOEFL[^0-9]{0,20}(\d+(?:\.\d+)?)", raw, re.I)
    if match:
        return {"test": "TOEFL", "minimum": float(match.group(1)), "raw": raw}
    match = re.search(r"GRE[^0-9]{0,20}(\d+(?:\.\d+)?)", raw, re.I)
    if match:
        return {"test": "GRE", "minimum": float(match.group(1)), "raw": raw}
    match = re.search(r"JLPT[^A-Z0-9]{0,12}(N[1-5])", raw, re.I)
    if match:
        return {"test": "JLPT", "minimum": match.group(1).upper(), "raw": raw}
    return {"test": None, "raw": raw}


def _level_value(levels: dict, test_name: str):
    if not isinstance(levels, dict):
        return None
    for key, value in levels.items():
        if str(key).upper() == test_name:
            return value
    return None


def _numeric_score(test: dict) -> float | None:
    scores = test.get("scores") or {}
    for key in ("overall", "total", "score"):
        if scores.get(key) not in (None, ""):
            try:
                return float(scores[key])
            except (TypeError, ValueError):
                return None
    return None


def _jlpt_level(value) -> str | None:
    if value is None:
        return None
    text = str(value).strip().upper()
    if text in JLPT_RANK:
        return text
    scores = value if isinstance(value, dict) else None
    if scores:
        return _jlpt_level(scores.get("level") or scores.get("overall"))
    return None


def _language_evidence(test_name: str, profile: dict, tests: list[dict] | None) -> tuple[str, object | None]:
    levels = profile.get("language_levels") or {}
    direct = _level_value(levels, test_name)
    if direct not in (None, ""):
        if test_name == "JLPT":
            level = _jlpt_level(direct)
            if level:
                return "profile", level
        else:
            try:
                return "profile", float(direct)
            except (TypeError, ValueError):
                pass
    for test in tests or []:
        if str(test.get("test_type") or "").upper() != test_name:
            continue
        if test_name == "JLPT":
            level = _jlpt_level((test.get("scores") or {}).get("level"))
            if level:
                return "test", level
        else:
            number = _numeric_score(test)
            if number is not None:
                return "test", number
    return "none", None


def _language_meets(test_name: str, minimum, actual) -> bool:
    if test_name == "JLPT":
        have = JLPT_RANK.get(str(actual).upper())
        need = JLPT_RANK.get(str(minimum).upper())
        if have is None or need is None:
            return False
        return have >= need
    return float(actual) + 1e-9 >= float(minimum)


def evaluate_constraints(constraints: dict, profile: dict, tests: list[dict] | None = None) -> dict:
    """Each rule is pass, fail, or unknown. Unknown does not block."""
    rules = []
    cutoff = constraints.get("gpa_cutoff")
    gpa = profile.get("gpa")
    if cutoff is None:
        rules.append({"rule": "gpa", "result": "unknown", "detail": "No GPA cutoff is listed."})
    elif gpa is None:
        rules.append({"rule": "gpa", "result": "unknown", "detail": "Your GPA is not entered."})
    elif float(gpa) + 1e-9 < float(cutoff):
        rules.append({
            "rule": "gpa",
            "result": "fail",
            "detail": f"GPA {gpa:g} is below the cutoff {float(cutoff):g}",
        })
    else:
        rules.append({
            "rule": "gpa",
            "result": "pass",
            "detail": f"GPA {gpa:g} meets the cutoff {float(cutoff):g}",
        })

    limit = constraints.get("age_limit")
    age = profile.get("age")
    if limit is None:
        rules.append({"rule": "age", "result": "unknown", "detail": "No age limit is listed."})
    elif age is None:
        rules.append({"rule": "age", "result": "unknown", "detail": "Your age is not entered."})
    elif int(age) > int(limit):
        rules.append({
            "rule": "age",
            "result": "fail",
            "detail": f"age {int(age)} is over the limit {int(limit)}",
        })
    else:
        rules.append({
            "rule": "age",
            "result": "pass",
            "detail": f"age {int(age)} is within the limit {int(limit)}",
        })

    parsed = parse_language_requirement(constraints.get("language_requirement") or "")
    if parsed is None:
        rules.append({"rule": "language", "result": "unknown", "detail": "No language requirement is listed."})
    elif not parsed.get("test"):
        rules.append({
            "rule": "language",
            "result": "unknown",
            "detail": "The language requirement could not be compared.",
        })
    else:
        source, actual = _language_evidence(parsed["test"], profile, tests)
        if source == "none":
            rules.append({
                "rule": "language",
                "result": "unknown",
                "detail": f"No {parsed['test']} score is on your profile.",
            })
        elif _language_meets(parsed["test"], parsed["minimum"], actual):
            rules.append({
                "rule": "language",
                "result": "pass",
                "detail": f"{parsed['test']} {actual} meets {parsed['minimum']}",
            })
        else:
            rules.append({
                "rule": "language",
                "result": "fail",
                "detail": f"{parsed['test']} {actual} is below {parsed['minimum']}",
            })

    if not str(profile.get("degree") or "").strip():
        rules.append({"rule": "degree", "result": "unknown", "detail": "Your degree is not entered."})
    else:
        rules.append({"rule": "degree", "result": "pass", "detail": "A degree is recorded on your profile."})
    if not str(profile.get("graduation_date") or "").strip():
        rules.append({"rule": "graduation", "result": "unknown", "detail": "Your graduation date is not entered."})
    else:
        rules.append({"rule": "graduation", "result": "pass", "detail": "A graduation date is recorded."})

    failures = [rule for rule in rules if rule["result"] == "fail" and rule["rule"] in {"gpa", "age", "language"}]
    if failures:
        badge = "blocked: " + "; ".join(rule["detail"] for rule in failures)
        blocked = True
    else:
        badge = "eligible"
        blocked = False
    return {"badge": badge, "blocked": blocked, "rules": rules}


def _clean_program_fields(data: dict, *, existing: dict | None = None) -> dict:
    source = existing or {}
    cleaned = {}
    for key in PROGRAM_TEXT:
        if key in data or existing is None:
            cleaned[key] = str(data.get(key) if key in data else source.get(key) or "").strip()
    if "application_fee" in data or existing is None:
        cleaned["application_fee"] = _optional_float(
            data.get("application_fee", source.get("application_fee")),
            "Application fee",
        )
    if "gpa_cutoff" in data or existing is None:
        cleaned["gpa_cutoff"] = _optional_float(data.get("gpa_cutoff", source.get("gpa_cutoff")), "GPA cutoff")
    if "age_limit" in data or existing is None:
        cleaned["age_limit"] = _optional_int(data.get("age_limit", source.get("age_limit")), "Age limit")
    for key in ("sop_word_limit", "sop_page_limit", "proposal_word_limit", "proposal_page_limit"):
        if key in data or existing is None:
            cleaned[key] = _optional_int(data.get(key, source.get(key)), key.replace("_", " "))
    return cleaned


def _require_program(program_id: int) -> dict:
    program = store.get_program_row(int(program_id))
    if program is None:
        raise MastersError("That program is not here.", 404)
    return program


def _template_for(country: str, program_type: str = "master") -> dict | None:
    templates = store.list_templates()
    country_key = country.strip().casefold()
    type_key = program_type.strip().casefold()
    exact = [
        item for item in templates
        if item["country"].strip().casefold() == country_key and item["program_type"].strip().casefold() == type_key and country_key
    ]
    if exact:
        return exact[0]
    by_country = [item for item in templates if item["country"].strip().casefold() == country_key and country_key]
    if by_country:
        return by_country[0]
    general = [item for item in templates if not item["country"].strip()]
    return general[0] if general else (templates[0] if templates else None)


def create_program(data: dict, *, apply_template: bool = True) -> dict:
    init()
    if data.get("status") == "submitted":
        raise MastersError("Status submitted is only set when you explicitly mark the program submitted.")
    status = data.get("status") or "shortlisted"
    if status not in {"shortlisted", "preparing"}:
        raise MastersError("A new program starts as shortlisted or preparing.")
    fields = _clean_program_fields(data)
    if not fields["university"]:
        raise MastersError("Enter a university.")
    fields["verified"] = False
    fields["verified_at"] = None
    fields["status"] = status
    program = store.insert_program(fields)
    if apply_template:
        template = _template_for(fields["country"])
        if template:
            _copy_template(program["id"], template)
    _audit("create_program", "program", program["id"], program["university"])
    return get_program(program["id"])


def update_program(program_id: int, data: dict) -> dict:
    program = _require_program(program_id)
    if data.get("status") == "submitted":
        raise MastersError("Status submitted is only set when you explicitly mark the program submitted.")
    if "status" in data and data.get("status") not in (None, "", program["status"]):
        raise MastersError("Change status with the status action. Submitted needs an explicit mark.")
    if "verified" in data or "verified_at" in data:
        raise MastersError("Verify a program with the verify action after you check the source.")
    fields = _clean_program_fields(data, existing=program)
    store.update_program_row(program["id"], fields)
    _audit("update_program", "program", program["id"], program["university"])
    return get_program(program["id"])


def delete_program(program_id: int) -> None:
    program = _require_program(program_id)
    store.delete_program_row(program["id"])
    _audit("delete_program", "program", program["id"], program["university"])


def list_programs(filters: dict | None = None, today: date | None = None) -> list[dict]:
    init()
    filters = filters or {}
    today = today or date.today()
    profile = _profile()
    tests = store.list_tests()
    deadlines = _annotate_deadlines(store.list_deadline_rows(), today)
    programs = []
    country = str(filters.get("country") or "").strip().casefold()
    intake = str(filters.get("intake") or "").strip().casefold()
    status = str(filters.get("status") or "").strip()
    start = _date_text(filters.get("deadline_from"), "Deadline from") if filters.get("deadline_from") else None
    end = _date_text(filters.get("deadline_to"), "Deadline to") if filters.get("deadline_to") else None
    for program in store.list_program_rows():
        if country and program["country"].casefold() != country:
            continue
        if intake and intake not in program["intake"].casefold():
            continue
        if status and program["status"] != status:
            continue
        relevant = _program_deadline_dates(program["id"], deadlines)
        if start or end:
            if not any(_in_range(day, start, end) for day in relevant):
                continue
        item = _with_eligibility(program, profile, tests)
        next_day = min(relevant) if relevant else None
        item["next_deadline"] = next_day
        if next_day:
            days, band = deadline_band(date.fromisoformat(next_day), today)
            item["days_left"] = days
            item["band"] = band
        else:
            item["days_left"] = None
            item["band"] = None
        item["stale"] = is_stale(program)
        item["stale_message"] = stale_message(program)
        programs.append(item)
    programs.sort(key=lambda item: (item["next_deadline"] is None, item["next_deadline"] or "", item["id"]))
    return programs


def _in_range(day: str, start: str | None, end: str | None) -> bool:
    if start and day < start:
        return False
    if end and day > end:
        return False
    return True


def _program_deadline_dates(program_id: int, deadlines: list[dict]) -> list[str]:
    dates = [item["date"] for item in deadlines if item.get("program_id") == program_id]
    for scholarship in store.linked_scholarships(program_id):
        if scholarship.get("deadline"):
            dates.append(scholarship["deadline"])
    return dates


def get_program(program_id: int, today: date | None = None) -> dict:
    program = _require_program(program_id)
    item = _with_eligibility(program, _profile(), store.list_tests())
    item["stale"] = is_stale(program)
    item["stale_message"] = stale_message(program)
    item["unverified"] = not program["verified"]
    return item


def program_detail(program_id: int, today: date | None = None) -> dict:
    today = today or date.today()
    program = get_program(program_id, today)
    deadlines = [
        item for item in list_deadlines(today)
        if item.get("program_id") == program["id"] or (
            item.get("scholarship_id") in {row["id"] for row in store.linked_scholarships(program["id"])}
        )
    ]
    return {
        "program": program,
        "deadlines": deadlines,
        "checklist": store.list_checklist(program["id"]),
        "writing": writing_bundle(program["id"]),
        "recommenders": store.list_assignments(program["id"]),
        "professors": [decorate_professor(row, today) for row in store.list_professors(program["id"])],
        "costs": cost_sheet(program["id"]),
        "scholarships": scholarships_for_program(program["id"]),
        "package": package_view(program["id"]),
        "tests": [fit for row in store.list_tests() for fit in [test_fit(row, program)]],
        "blockers": review_blockers(program["id"]),
    }


def _profile() -> dict:
    academic = store.get_academic_profile()
    job = db.get_profile()
    academic["name"] = job.get("name") or ""
    academic["contact"] = job.get("contact") or ""
    academic["resume_text"] = job.get("resume_text") or ""
    return academic


def _with_eligibility(program: dict, profile: dict, tests: list[dict]) -> dict:
    item = dict(program)
    item["eligibility"] = evaluate_constraints(program, profile, tests)
    return item


def set_status(program_id: int, status: str) -> dict:
    program = _require_program(program_id)
    status = (status or "").strip()
    if status == "submitted":
        raise MastersError("Status submitted is only set when you explicitly mark the program submitted.")
    if status not in PROGRAM_STATUSES:
        raise MastersError("Choose a status from the pipeline.")
    if status in OUTCOMES and program["status"] != "submitted":
        raise MastersError("Record an outcome after you have marked the application submitted.")
    if status == "ready_for_review":
        blockers = review_blockers(program["id"])
        if blockers:
            raise MastersError("Not ready for review. " + " ".join(blockers))
    store.update_program_row(program["id"], {"status": status})
    _audit("set_status", "program", program["id"], status)
    return get_program(program["id"])


def mark_submitted(program_id: int) -> dict:
    program = _require_program(program_id)
    if program["status"] == "submitted":
        return get_program(program["id"])
    if program["status"] != "ready_for_review":
        raise MastersError(
            "Mark submitted only after the program is ready for review, and only with the mark submitted action. "
            "You send the application yourself."
        )
    blockers = review_blockers(program["id"])
    if blockers:
        raise MastersError("Not ready to mark submitted. " + " ".join(blockers))
    store.update_program_row(program["id"], {"status": "submitted"})
    _audit("mark_submitted", "program", program["id"], "You marked this application submitted after sending it yourself.")
    return get_program(program["id"])


def verify_program(program_id: int, when: datetime | None = None) -> dict:
    program = _require_program(program_id)
    stamp = (when or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()
    store.update_program_row(program["id"], {"verified": 1, "verified_at": stamp})
    _audit("verify_program", "program", program["id"], "Checked against the source.")
    return get_program(program["id"])


def review_blockers(program_id: int) -> list[str]:
    items = store.list_checklist(program_id)
    blockers = []
    for item in items:
        if item["required"] and item["status"] != "done":
            blockers.append(f"{item['label']} is not done.")
    if any(item["required"] and item["kind"] == "sop" for item in items):
        approved = [
            row for row in store.list_writings(kind="sop", program_id=program_id, scope="variant")
            if row["status"] == "approved"
        ]
        if not approved:
            blockers.append("An approved SOP variant is required.")
    return blockers


def _copy_template(program_id: int, template: dict) -> list[dict]:
    items = []
    for raw in template["items"]:
        items.append({
            "kind": raw["kind"],
            "label": raw["label"],
            "required": bool(raw.get("required", True)),
            "status": "todo",
            "lor_count": raw.get("lor_count"),
            "document_id": None,
        })
    return store.replace_checklist(program_id, items)


def list_templates() -> list[dict]:
    init()
    return store.list_templates()


def create_template(data: dict) -> dict:
    init()
    name = str(data.get("name") or "").strip()
    if not name:
        raise MastersError("Name the checklist template.")
    items = [_clean_template_item(item) for item in data.get("items") or []]
    if not items:
        raise MastersError("Add at least one checklist item.")
    return store.insert_template({
        "name": name,
        "country": str(data.get("country") or "").strip(),
        "program_type": str(data.get("program_type") or "master").strip() or "master",
        "items": items,
    })


def _clean_template_item(item: dict) -> dict:
    kind = str(item.get("kind") or "custom").strip()
    if kind not in CHECKLIST_KINDS:
        raise MastersError("That checklist item type is not supported.")
    label = str(item.get("label") or kind).strip()
    if not label:
        raise MastersError("Each checklist item needs a label.")
    cleaned = {"kind": kind, "label": label, "required": bool(item.get("required", True))}
    if kind == "lor":
        cleaned["lor_count"] = _optional_int(item.get("lor_count") or 2, "LOR count") or 2
    elif item.get("lor_count") not in (None, ""):
        cleaned["lor_count"] = _optional_int(item.get("lor_count"), "LOR count")
    return cleaned


def apply_template(program_id: int, template_id: int) -> list[dict]:
    _require_program(program_id)
    template = store.get_template(int(template_id))
    if template is None:
        raise MastersError("That checklist template is not here.", 404)
    items = _copy_template(program_id, template)
    _audit("apply_template", "program", program_id, template["name"])
    return items


def add_checklist_item(program_id: int, data: dict) -> dict:
    _require_program(program_id)
    cleaned = _clean_template_item(data)
    cleaned.update({
        "program_id": program_id,
        "status": _checklist_status(data.get("status") or "todo"),
        "document_id": _document_link(data.get("document_id")),
    })
    return store.insert_checklist_item(cleaned)


def update_checklist_item(item_id: int, data: dict) -> dict:
    item = store.get_checklist_item(int(item_id))
    if item is None:
        raise MastersError("That checklist item is not here.", 404)
    fields = {}
    if "status" in data:
        fields["status"] = _checklist_status(data.get("status"))
    if "required" in data:
        fields["required"] = bool(data.get("required"))
    if "label" in data:
        label = str(data.get("label") or "").strip()
        if not label:
            raise MastersError("The checklist item needs a label.")
        fields["label"] = label
    if "document_id" in data:
        fields["document_id"] = _document_link(data.get("document_id"))
    if "lor_count" in data:
        fields["lor_count"] = _optional_int(data.get("lor_count"), "LOR count")
    updated = store.update_checklist_item(item["id"], fields)
    return updated


def delete_checklist_item(item_id: int) -> None:
    item = store.get_checklist_item(int(item_id))
    if item is None:
        raise MastersError("That checklist item is not here.", 404)
    store.delete_checklist_item(item["id"])


def _checklist_status(status: str) -> str:
    if status not in CHECKLIST_STATUSES:
        raise MastersError("Checklist status is todo, in progress, or done.")
    return status


def _document_link(document_id):
    if document_id in (None, "", 0):
        return None
    document = store.get_document(int(document_id))
    if document is None:
        raise MastersError("That document is not in the vault.", 404)
    return document["id"]


def _annotate_deadlines(rows: list[dict], today: date) -> list[dict]:
    programs = {item["id"]: item for item in store.list_program_rows()}
    scholarships = {item["id"]: item for item in store.list_scholarships()}
    annotated = []
    for row in rows:
        item = dict(row)
        day = date.fromisoformat(item["date"][:10])
        days, band = deadline_band(day, today)
        item["days_left"] = days
        item["band"] = band
        program = programs.get(item.get("program_id"))
        scholarship = scholarships.get(item.get("scholarship_id"))
        item["program_label"] = ""
        if program:
            item["program_label"] = f"{program['university']} — {program['degree']}".strip(" —")
        item["scholarship_name"] = scholarship["name"] if scholarship else ""
        item["reminders"] = store.list_reminder_rows(item["id"])
        annotated.append(item)
    annotated.sort(key=lambda item: (item["date"], item["id"]))
    return annotated


def list_deadlines(today: date | None = None) -> list[dict]:
    init()
    return _annotate_deadlines(store.list_deadline_rows(), today or date.today())


def add_deadline(program_id: int, data: dict) -> dict:
    _require_program(program_id)
    fields = _clean_deadline(data)
    fields["program_id"] = program_id
    fields["scholarship_id"] = None
    created = store.insert_deadline(fields)
    _audit("add_deadline", "deadline", created["id"], f"{fields['type']} {fields['date']}")
    return _one_deadline(created["id"])


def update_deadline(deadline_id: int, data: dict) -> dict:
    current = store.get_deadline_row(int(deadline_id))
    if current is None:
        raise MastersError("That deadline is not here.", 404)
    fields = _clean_deadline({**current, **data})
    store.update_deadline_row(current["id"], fields)
    return _one_deadline(current["id"])


def delete_deadline(deadline_id: int) -> None:
    current = store.get_deadline_row(int(deadline_id))
    if current is None:
        raise MastersError("That deadline is not here.", 404)
    store.delete_deadline_row(current["id"])


def _clean_deadline(data: dict) -> dict:
    kind = str(data.get("type") or "").strip()
    if kind not in DEADLINE_TYPES:
        raise MastersError("Deadline type must be application, documents, recommender, scholarship, or test score.")
    day = _date_text(data.get("date"), "Deadline date")
    if not day:
        raise MastersError("Enter a deadline date.")
    status = str(data.get("status") or "open").strip()
    if status not in {"open", "done"}:
        raise MastersError("Deadline status is open or done.")
    timezone_name = str(data.get("timezone") or "UTC").strip() or "UTC"
    return {"type": kind, "date": day, "timezone": timezone_name[:64], "status": status}


def _one_deadline(deadline_id: int) -> dict:
    rows = [row for row in list_deadlines() if row["id"] == deadline_id]
    if not rows:
        raise MastersError("That deadline is not here.", 404)
    return rows[0]


def list_reminders(deadline_id: int | None = None) -> list[dict]:
    init()
    rows = store.list_reminder_rows(deadline_id)
    for row in rows:
        row["channel"] = row.get("channel") or "in_app"
        row["sent"] = False
    return rows


def draft_reminder_email(reminder_id: int) -> dict:
    reminder = store.get_reminder_row(int(reminder_id))
    if reminder is None:
        raise MastersError("That reminder is not here.", 404)
    deadline = store.get_deadline_row(reminder["deadline_id"])
    program = store.get_program_row(deadline["program_id"]) if deadline and deadline.get("program_id") else None
    scholarship = store.get_scholarship(deadline["scholarship_id"]) if deadline and deadline.get("scholarship_id") else None
    label = program["university"] if program else (scholarship["name"] if scholarship else "your application")
    subject = f"Reminder: {deadline['type']} deadline for {label}"
    body = _template_banner(
        f"This is an in-app reminder {reminder['offset_days']} days before {deadline['date']} ({deadline['timezone']}).\n\n"
        f"Deadline type: {deadline['type']}\n"
        f"Program or scholarship: {label}\n\n"
        "Nothing has been emailed. Copy this after you approve it if you want to send it yourself."
    )
    email = store.insert_email({
        "kind": "deadline_reminder",
        "subject": subject,
        "body": body,
        "to_address": "",
        "program_id": deadline.get("program_id") if deadline else None,
        "related_type": "reminder",
        "related_id": reminder["id"],
    })
    store.set_reminder_email(reminder["id"], email["id"])
    _audit("draft_reminder_email", "email", email["id"], "In-app reminder drafted. Not sent.")
    return email


def save_document(filename: str, data: bytes, content_type: str, doc_type: str, is_master: bool) -> dict:
    init()
    if len(data) > MAX_UPLOAD_BYTES:
        raise MastersError("That file is larger than 8 MB.")
    if not data:
        raise MastersError("That file is empty.")
    kind = (doc_type or "").strip()
    if kind not in DOC_TYPES:
        raise MastersError("Document type must be transcript, CV, passport, certificate, photo, SOP, research proposal, or other.")
    sniffed = _sniff(data)
    safe_name = _safe_filename(filename, sniffed)
    version = store.next_document_version(kind)
    stored_name = f"{uuid.uuid4().hex}{_suffix(sniffed)}"
    path = store.vault_dir() / stored_name
    path.write_bytes(data)
    document = store.insert_document({
        "doc_type": kind,
        "version": version,
        "original_name": safe_name,
        "stored_name": stored_name,
        "content_type": sniffed,
        "size_bytes": len(data),
        "is_master": bool(is_master),
        "uploaded_at": store.now(),
    })
    _audit("upload_document", "document", document["id"], document["original_name"])
    return document_view(document["id"])


def _suffix(content_type: str) -> str:
    return {
        "application/pdf": ".pdf",
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/gif": ".gif",
        "image/webp": ".webp",
    }[content_type]


def _sniff(data: bytes) -> str:
    if data.startswith(b"%PDF"):
        return "application/pdf"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if len(data) >= 12 and data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    raise MastersError("Upload a PDF or an image (PNG, JPEG, GIF, or WebP).")


def _safe_filename(filename: str, content_type: str) -> str:
    base = re.sub(r"[^A-Za-z0-9._-]+", "-", (filename or "document").rsplit("/", 1)[-1]).strip(".-") or "document"
    return base[:120]


def list_documents() -> list[dict]:
    init()
    return [document_view(item["id"]) for item in store.list_documents()]


def document_view(document_id: int) -> dict:
    document = store.get_document(int(document_id))
    if document is None:
        raise MastersError("That document is not in the vault.", 404)
    programs = store.programs_for_document(document["id"])
    document["programs"] = [
        {"id": item["id"], "university": item["university"], "degree": item["degree"], "country": item["country"]}
        for item in programs
    ]
    return document


def document_file(document_id: int) -> tuple[dict, Path]:
    document = store.get_document(int(document_id))
    if document is None:
        raise MastersError("That document is not in the vault.", 404)
    root = store.vault_dir().resolve()
    path = (root / document["stored_name"]).resolve()
    if root not in path.parents:
        raise MastersError("That file is not in the vault.", 404)
    if not path.is_file():
        raise MastersError("The file is missing from the vault.", 404)
    return document, path


def delete_document(document_id: int) -> None:
    document = store.delete_document_row(int(document_id))
    if document is None:
        raise MastersError("That document is not in the vault.", 404)
    path = store.vault_dir() / document["stored_name"]
    if path.is_file():
        path.unlink()
    _audit("delete_document", "document", document["id"], document["original_name"])


def get_academic_profile() -> dict:
    init()
    return _profile()


def save_academic_profile(data: dict) -> dict:
    init()
    current = store.get_academic_profile()
    levels = data.get("language_levels", current.get("language_levels") or {})
    if not isinstance(levels, dict):
        raise MastersError("Language levels must be a set of test names and scores.")
    cleaned_levels = {}
    for key, value in levels.items():
        name = str(key).strip().upper()
        if name not in TEST_TYPES or value in (None, ""):
            continue
        if name == "JLPT":
            level = _jlpt_level(value)
            if level is None:
                raise MastersError("JLPT level must be N1, N2, N3, N4, or N5.")
            cleaned_levels[name] = level
        else:
            cleaned_levels[name] = _optional_float(value, name)
    store.save_academic_profile({
        "gpa": _optional_float(data.get("gpa", current.get("gpa")), "GPA"),
        "gpa_scale": _optional_float(data.get("gpa_scale", current.get("gpa_scale")), "GPA scale"),
        "degree": str(data.get("degree", current.get("degree") or "")).strip(),
        "graduation_date": _date_text(data.get("graduation_date", current.get("graduation_date")), "Graduation date") or "",
        "age": _optional_int(data.get("age", current.get("age")), "Age"),
        "language_levels": cleaned_levels,
        "publications": _optional_int(data.get("publications", current.get("publications")), "Publications"),
    })
    _audit("save_profile", "academic_profile", 1, "Academic profile updated.")
    return get_academic_profile()


def ensure_master(kind: str) -> dict:
    init()
    if kind not in WRITING_KINDS:
        raise MastersError("Writing is either an SOP or a research proposal.")
    rows = store.list_writings(kind=kind, scope="master")
    if rows:
        return _writing_view(rows[0])
    created = store.insert_writing({
        "kind": kind,
        "scope": "master",
        "program_id": None,
        "title": "Master statement of purpose" if kind == "sop" else "Master research proposal",
        "body": "",
        "status": "draft",
        "origin": "user",
    })
    return _writing_view(created)


def save_master(kind: str, body: str, title: str | None = None) -> dict:
    master = ensure_master(kind)
    fields = {"body": body or "", "origin": "user", "status": "draft"}
    if title is not None:
        fields["title"] = title.strip()
    updated = store.update_writing(master["id"], fields)
    return _writing_view(updated)


def writing_bundle(program_id: int | None = None) -> dict:
    init()
    masters = {"sop": ensure_master("sop"), "research_proposal": ensure_master("research_proposal")}
    variants = []
    if program_id is not None:
        _require_program(program_id)
        variants = [_writing_view(row) for row in store.list_writings(program_id=program_id, scope="variant")]
    return {"masters": masters, "variants": variants}


def _writing_view(row: dict) -> dict:
    item = dict(row)
    item["word_count"] = word_count(item.get("body") or "")
    item["page_estimate"] = page_estimate(item.get("body") or "")
    limit = item.get("word_limit")
    item["over_word_limit"] = bool(limit) and item["word_count"] > int(limit)
    page_limit = item.get("page_limit")
    item["over_page_limit"] = bool(page_limit) and item["page_estimate"] > int(page_limit)
    return item


def update_writing(writing_id: int, data: dict) -> dict:
    row = store.get_writing(int(writing_id))
    if row is None:
        raise MastersError("That draft is not here.", 404)
    if row["status"] == "approved" and "body" in data and data.get("body") != row["body"]:
        # Editing an approved variant returns it to draft. It cannot stay attached.
        fields = {"body": data.get("body") or "", "status": "draft"}
        if "title" in data:
            fields["title"] = str(data.get("title") or "").strip()
        updated = store.update_writing(row["id"], fields)
        _detach_if_needed(updated)
        return _writing_view(updated)
    fields = {}
    if "body" in data:
        fields["body"] = data.get("body") or ""
    if "title" in data:
        fields["title"] = str(data.get("title") or "").strip()
    updated = store.update_writing(row["id"], fields)
    return _writing_view(updated)


def _detach_if_needed(writing: dict) -> None:
    if writing["scope"] != "variant" or not writing.get("program_id"):
        return
    package = store.get_package(writing["program_id"])
    if package is None:
        return
    fields = {}
    if package.get("sop_variant_id") == writing["id"] and writing["status"] != "approved":
        fields["sop_variant_id"] = None
        fields["status"] = "draft"
    if package.get("proposal_variant_id") == writing["id"] and writing["status"] != "approved":
        fields["proposal_variant_id"] = None
        if package.get("status") == "approved":
            fields["status"] = "draft"
    if fields:
        store.update_package(writing["program_id"], fields)


def approve_writing(writing_id: int, body: str | None = None, title: str | None = None) -> dict:
    row = store.get_writing(int(writing_id))
    if row is None:
        raise MastersError("That draft is not here.", 404)
    if row["scope"] != "variant":
        raise MastersError("Approve a program variant. The master text stays the source you edit.")
    fields = {"status": "approved"}
    if body is not None:
        fields["body"] = body
    if title is not None:
        fields["title"] = title.strip()
    updated = store.update_writing(row["id"], fields)
    _audit("approve_writing", "writing", updated["id"], updated["kind"])
    return _writing_view(updated)


def reject_writing(writing_id: int) -> dict:
    row = store.get_writing(int(writing_id))
    if row is None:
        raise MastersError("That draft is not here.", 404)
    if row["scope"] != "variant":
        raise MastersError("The master text is not an approval item.")
    updated = store.update_writing(row["id"], {"status": "rejected"})
    _detach_if_needed(updated)
    _audit("reject_writing", "writing", updated["id"], updated["kind"])
    return _writing_view(updated)


async def generate_variant(program_id: int, kind: str) -> dict:
    program = _require_program(program_id)
    if kind not in WRITING_KINDS:
        raise MastersError("Generate an SOP or a research proposal variant.")
    master = ensure_master(kind)
    profile = _profile()
    settings = db.get_settings()
    origin = "template"
    body = template_variant(kind, master.get("body") or "", program, profile)
    if configured(settings):
        try:
            drafted = await complete(settings, _variant_messages(kind, master, program, profile), timeout=20)
            drafted = (drafted or "").strip()
            if drafted:
                body = "DRAFT for your review. Nothing has been submitted.\n\n" + drafted
                origin = "model"
        except (ModelError, json.JSONDecodeError, TimeoutError, OSError):
            origin = "template"
    word_limit = program["sop_word_limit"] if kind == "sop" else program["proposal_word_limit"]
    page_limit = program["sop_page_limit"] if kind == "sop" else program["proposal_page_limit"]
    title = f"{'SOP' if kind == 'sop' else 'Research proposal'} — {program['university']}"
    created = store.insert_writing({
        "kind": kind,
        "scope": "variant",
        "program_id": program["id"],
        "title": title,
        "body": body,
        "word_limit": word_limit,
        "page_limit": page_limit,
        "status": "draft",
        "origin": origin,
    })
    _audit("generate_variant", "writing", created["id"], origin)
    return _writing_view(created)


def template_variant(kind: str, master_text: str, program: dict, profile: dict) -> str:
    label = "statement of purpose" if kind == "sop" else "research proposal"
    degree = program.get("degree") or "a master's program"
    university = program.get("university") or "the university"
    lines = [
        "TEMPLATE DRAFT (no model connected). Built from your master text and this program's fields. Review it before you approve it.",
        "",
        f"Draft {label} for {degree} at {university}"
        + (f", {program['department']}" if program.get("department") else "")
        + (f", {program['country']}" if program.get("country") else "")
        + ".",
        f"Intake: {program.get('intake') or 'not set'}.",
        f"Applicant: {profile.get('name') or 'your name'}. Degree: {profile.get('degree') or 'not entered'}. "
        f"GPA: {profile.get('gpa') if profile.get('gpa') is not None else 'not entered'}.",
        f"Research areas: {program.get('research_areas') or 'not listed'}.",
        f"Requirements noted on the program: {program.get('requirements_text') or program.get('language_requirement') or 'not listed'}.",
        "",
        "Master text:",
        master_text.strip() or "(The master text is still empty. Write it, then generate again.)",
        "",
        "This is a draft for you to edit. It has not been approved and nothing has been submitted.",
    ]
    return "\n".join(lines)


def _template_banner(body: str) -> str:
    return (
        "TEMPLATE DRAFT (no model connected). Nothing has been sent.\n\n"
        + body.strip()
        + "\n\nApprove this only when you are ready to copy it. Approving does not send it."
    )


def _variant_messages(kind: str, master: dict, program: dict, profile: dict) -> list[dict]:
    label = "statement of purpose" if kind == "sop" else "research proposal"
    system = (
        f"Draft a {label} variant for one master's application. "
        "Use only the master text, the applicant profile, and the program fields. "
        "Do not invent degrees, grades, employers, publications, ages, or test scores. "
        "Do not say the application was submitted. "
        "Return only the draft text."
    )
    user = (
        f"Master text:\n{master.get('body') or ''}\n\n"
        f"Name: {profile.get('name') or ''}\n"
        f"Degree: {profile.get('degree') or ''}\n"
        f"GPA: {profile.get('gpa')}\n"
        f"Graduation: {profile.get('graduation_date') or ''}\n"
        f"Publications: {profile.get('publications')}\n"
        f"Language levels: {json.dumps(profile.get('language_levels') or {})}\n"
        f"Resume excerpt:\n{(profile.get('resume_text') or '')[:4000]}\n\n"
        f"University: {program.get('university') or ''}\n"
        f"Department: {program.get('department') or ''}\n"
        f"Degree applied for: {program.get('degree') or ''}\n"
        f"Country: {program.get('country') or ''}\n"
        f"Intake: {program.get('intake') or ''}\n"
        f"Research areas: {program.get('research_areas') or ''}\n"
        f"Requirements: {program.get('requirements_text') or ''}\n"
        f"Language requirement: {program.get('language_requirement') or ''}\n"
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def attach_variant(program_id: int, writing_id: int) -> dict:
    program = _require_program(program_id)
    writing = store.get_writing(int(writing_id))
    if writing is None:
        raise MastersError("That draft is not here.", 404)
    if writing["scope"] != "variant" or writing.get("program_id") != program["id"]:
        raise MastersError("Attach a variant written for this program.")
    if writing["status"] != "approved":
        raise MastersError("Only an approved variant can be attached to a submission package.")
    if writing["kind"] == "sop":
        fields = {"sop_variant_id": writing["id"]}
    elif writing["kind"] == "research_proposal":
        fields = {"proposal_variant_id": writing["id"]}
    else:
        raise MastersError("Attach an SOP or a research proposal.")
    store.update_package(program["id"], fields)
    _audit("attach_variant", "package", program["id"], f"{writing['kind']} {writing['id']}")
    return package_view(program["id"])


def package_view(program_id: int) -> dict:
    program = _require_program(program_id)
    package = store.get_package(program["id"]) or {
        "program_id": program["id"],
        "sop_variant_id": None,
        "proposal_variant_id": None,
        "status": "draft",
    }
    checklist = store.list_checklist(program["id"])
    documents = []
    seen = set()
    for item in checklist:
        if not item.get("document_id") or item["document_id"] in seen:
            continue
        seen.add(item["document_id"])
        document = store.get_document(item["document_id"])
        if document:
            documents.append(document)
    sop = store.get_writing(package["sop_variant_id"]) if package.get("sop_variant_id") else None
    proposal = store.get_writing(package["proposal_variant_id"]) if package.get("proposal_variant_id") else None
    if sop and sop["status"] != "approved":
        sop = None
    if proposal and proposal["status"] != "approved":
        proposal = None
    return {
        "program_id": program["id"],
        "status": package.get("status") or "draft",
        "portal_url": program["portal_url"],
        "application_url": program["application_url"],
        "checklist": checklist,
        "documents": documents,
        "sop_variant": _writing_view(sop) if sop else None,
        "proposal_variant": _writing_view(proposal) if proposal else None,
        "blockers": review_blockers(program["id"]),
        "program_status": program["status"],
    }


def approve_package(program_id: int) -> dict:
    program = _require_program(program_id)
    blockers = review_blockers(program["id"])
    if blockers:
        raise MastersError("The package is not complete. " + " ".join(blockers))
    package = package_view(program["id"])
    needs_sop = any(item["required"] and item["kind"] == "sop" for item in package["checklist"])
    if needs_sop and not package["sop_variant"]:
        raise MastersError("Attach an approved SOP variant before approving the package.")
    store.update_package(program["id"], {"status": "approved"})
    _audit("approve_package", "package", program["id"], "Package approved for your own submission. Not submitted.")
    return package_view(program["id"])


def reject_package(program_id: int) -> dict:
    program = _require_program(program_id)
    store.update_package(program["id"], {"status": "rejected"})
    _audit("reject_package", "package", program["id"], "Package sent back.")
    return package_view(program["id"])


def create_recommender(data: dict) -> dict:
    init()
    name = str(data.get("name") or "").strip()
    if not name:
        raise MastersError("Enter the recommender's name.")
    return store.insert_recommender({
        "name": name,
        "title": str(data.get("title") or "").strip(),
        "institution": str(data.get("institution") or "").strip(),
        "email": str(data.get("email") or "").strip(),
    })


def update_recommender(recommender_id: int, data: dict) -> dict:
    current = store.get_recommender(int(recommender_id))
    if current is None:
        raise MastersError("That recommender is not here.", 404)
    fields = {}
    for key in ("name", "title", "institution", "email"):
        if key in data:
            fields[key] = str(data.get(key) or "").strip()
    if "name" in fields and not fields["name"]:
        raise MastersError("Enter the recommender's name.")
    return store.update_recommender(current["id"], fields)


def list_recommenders() -> list[dict]:
    init()
    people = store.list_recommenders()
    assignments = store.list_assignments()
    for person in people:
        person["assignments"] = [item for item in assignments if item["recommender_id"] == person["id"]]
    return people


def delete_recommender(recommender_id: int) -> None:
    current = store.get_recommender(int(recommender_id))
    if current is None:
        raise MastersError("That recommender is not here.", 404)
    store.delete_recommender(current["id"])


def assign_recommender(recommender_id: int, data: dict) -> dict:
    person = store.get_recommender(int(recommender_id))
    if person is None:
        raise MastersError("That recommender is not here.", 404)
    program = _require_program(int(data.get("program_id")))
    status = str(data.get("status") or "not_asked")
    if status not in RECOMMENDER_STATUSES:
        raise MastersError("Recommender status is not asked, asked, or submitted.")
    if status == "submitted":
        raise MastersError("Submitted is a manual mark after the recommendation is actually in. Create the row first.")
    return store.insert_assignment({
        "recommender_id": person["id"],
        "program_id": program["id"],
        "requested_at": _date_text(data.get("requested_at"), "Requested date"),
        "due_date": _date_text(data.get("due_date"), "Due date"),
        "status": status,
    })


def update_assignment(assignment_id: int, data: dict) -> dict:
    current = store.get_assignment(int(assignment_id))
    if current is None:
        raise MastersError("That recommender assignment is not here.", 404)
    fields = {}
    if "requested_at" in data:
        fields["requested_at"] = _date_text(data.get("requested_at"), "Requested date")
    if "due_date" in data:
        fields["due_date"] = _date_text(data.get("due_date"), "Due date")
    if "status" in data:
        status = str(data.get("status") or "")
        if status not in RECOMMENDER_STATUSES:
            raise MastersError("Recommender status is not asked, asked, or submitted.")
        if status == "submitted":
            raise MastersError("Use the mark submitted action. Approving an email does not mark a recommendation submitted.")
        fields["status"] = status
    return store.update_assignment(current["id"], fields)


def mark_recommender_submitted(assignment_id: int) -> dict:
    current = store.get_assignment(int(assignment_id))
    if current is None:
        raise MastersError("That recommender assignment is not here.", 404)
    updated = store.update_assignment(current["id"], {"status": "submitted"})
    _audit("mark_recommender_submitted", "recommender_assignment", current["id"], current["name"])
    return updated


async def draft_recommender_email(assignment_id: int, kind: str) -> dict:
    if kind not in {"request", "reminder"}:
        raise MastersError("Draft a request or a reminder.")
    assignment = store.get_assignment(int(assignment_id))
    if assignment is None:
        raise MastersError("That recommender assignment is not here.", 404)
    program = _require_program(assignment["program_id"])
    profile = _profile()
    email_kind = "recommender_request" if kind == "request" else "recommender_reminder"
    subject, body, origin = await _email_draft(
        email_kind,
        _recommender_template(kind, assignment, program, profile),
        _recommender_prompt(kind, assignment, program, profile),
    )
    email = store.insert_email({
        "kind": email_kind,
        "subject": subject,
        "body": body,
        "to_address": assignment.get("email") or "",
        "program_id": program["id"],
        "related_type": "recommender_assignment",
        "related_id": assignment["id"],
    })
    log = list(assignment.get("reminder_log") or [])
    log.append({"at": store.now(), "email_id": email["id"], "kind": email_kind, "origin": origin, "sent": False})
    store.update_assignment(assignment["id"], {"reminder_log": log})
    _audit("draft_email", "email", email["id"], email_kind)
    return email


def _recommender_template(kind: str, assignment: dict, program: dict, profile: dict) -> tuple[str, str]:
    action = "ask whether you would write a recommendation" if kind == "request" else "send a short reminder about the recommendation"
    subject = f"{'Recommendation request' if kind == 'request' else 'Recommendation reminder'}: {program['university']}"
    degree = program.get("degree") or "master's"
    body = _template_banner(
        f"Dear {assignment['name']},\n\n"
        f"I am {profile.get('name') or 'an applicant'} and I am writing to {action} "
        f"for the {degree} program at {program['university']}"
        f"{' (' + program['department'] + ')' if program.get('department') else ''}, intake {program.get('intake') or 'the upcoming intake'}.\n\n"
        f"Due date recorded here: {assignment.get('due_date') or 'not set'}.\n"
        f"My degree: {profile.get('degree') or 'not entered'}. GPA: {profile.get('gpa') if profile.get('gpa') is not None else 'not entered'}.\n\n"
        "Thank you,\n"
        f"{profile.get('name') or ''}"
    )
    return subject, body


def _recommender_prompt(kind: str, assignment: dict, program: dict, profile: dict) -> str:
    label = "a recommendation request" if kind == "request" else "a recommendation reminder"
    return (
        f"Write {label} email. Do not say it has been sent. Do not invent facts.\n"
        f"To: {assignment['name']}, {assignment.get('title') or ''}, {assignment.get('institution') or ''}\n"
        f"Applicant: {profile.get('name') or ''}, degree {profile.get('degree') or ''}, GPA {profile.get('gpa')}\n"
        f"Program: {program.get('degree') or ''} at {program['university']}, {program.get('department') or ''}, intake {program.get('intake') or ''}\n"
        f"Due: {assignment.get('due_date') or ''}\n"
        'Return JSON: {"subject":"...","body":"..."}'
    )


def create_professor(data: dict) -> dict:
    init()
    name = str(data.get("professor") or data.get("name") or "").strip()
    if not name:
        raise MastersError("Enter the professor's name.")
    program_id = data.get("program_id")
    if program_id not in (None, ""):
        program_id = _require_program(int(program_id))["id"]
    else:
        program_id = None
    status = str(data.get("status") or "identified")
    if status not in PROFESSOR_STATUSES:
        raise MastersError("Professor status is identified, emailed, replied, meeting, or no response.")
    days = data.get("followup_after_days", 7)
    if days in (None, ""):
        days = 7
    days = _optional_int(days, "Follow-up days")
    if days is None or days < 1:
        raise MastersError("Follow-up days must be at least 1. The default is 7.")
    created = store.insert_professor({
        "professor": name,
        "lab": str(data.get("lab") or "").strip(),
        "university": str(data.get("university") or "").strip(),
        "research_area": str(data.get("research_area") or "").strip(),
        "program_id": program_id,
        "status": status,
        "followup_after_days": days,
        "emailed_at": _date_text(data.get("emailed_at"), "Emailed date"),
    })
    return decorate_professor(created)


def update_professor(professor_id: int, data: dict) -> dict:
    current = store.get_professor(int(professor_id))
    if current is None:
        raise MastersError("That professor is not here.", 404)
    fields = {}
    for key in ("professor", "lab", "university", "research_area"):
        if key in data:
            fields[key] = str(data.get(key) or "").strip()
    if "professor" in fields and not fields["professor"]:
        raise MastersError("Enter the professor's name.")
    if "program_id" in data:
        if data.get("program_id") in (None, ""):
            fields["program_id"] = None
        else:
            fields["program_id"] = _require_program(int(data["program_id"]))["id"]
    if "status" in data:
        status = str(data.get("status") or "")
        if status not in PROFESSOR_STATUSES:
            raise MastersError("Professor status is identified, emailed, replied, meeting, or no response.")
        fields["status"] = status
    if "followup_after_days" in data:
        days = _optional_int(data.get("followup_after_days"), "Follow-up days")
        if days is None or days < 1:
            raise MastersError("Follow-up days must be at least 1.")
        fields["followup_after_days"] = days
    if "emailed_at" in data:
        fields["emailed_at"] = _date_text(data.get("emailed_at"), "Emailed date")
    updated = store.update_professor(current["id"], fields)
    return decorate_professor(updated)


def list_professors() -> list[dict]:
    init()
    return [decorate_professor(row) for row in store.list_professors()]


def delete_professor(professor_id: int) -> None:
    current = store.get_professor(int(professor_id))
    if current is None:
        raise MastersError("That professor is not here.", 404)
    store.delete_professor(current["id"])


def decorate_professor(row: dict, today: date | None = None) -> dict:
    today = today or date.today()
    item = dict(row)
    item["followup_on"] = None
    item["followup_due"] = False
    if item["status"] in {"emailed", "no_response"} and item.get("emailed_at"):
        emailed = date.fromisoformat(item["emailed_at"][:10])
        follow = emailed + timedelta(days=int(item["followup_after_days"] or 7))
        item["followup_on"] = follow.isoformat()
        item["followup_due"] = follow <= today
    item["followup_sent"] = False
    return item


async def draft_professor_email(professor_id: int, kind: str = "contact") -> dict:
    if kind not in {"contact", "followup"}:
        raise MastersError("Draft a contact email or a follow-up.")
    person = store.get_professor(int(professor_id))
    if person is None:
        raise MastersError("That professor is not here.", 404)
    program = store.get_program_row(person["program_id"]) if person.get("program_id") else None
    profile = _profile()
    email_kind = "professor_contact" if kind == "contact" else "professor_followup"
    subject, body, origin = await _email_draft(
        email_kind,
        _professor_template(kind, person, program, profile),
        _professor_prompt(kind, person, program, profile),
    )
    email = store.insert_email({
        "kind": email_kind,
        "subject": subject,
        "body": body,
        "to_address": "",
        "program_id": person.get("program_id"),
        "related_type": "professor",
        "related_id": person["id"],
    })
    _audit("draft_email", "email", email["id"], f"{email_kind} not sent ({origin})")
    return email


def _professor_template(kind: str, person: dict, program: dict | None, profile: dict) -> tuple[str, str]:
    program_line = ""
    if program:
        degree = program.get("degree") or "master's"
        program_line = (
            f"I am preparing an application to the {degree} program at {program['university']}"
            f"{' (' + program['department'] + ')' if program.get('department') else ''}.\n"
        )
    if kind == "followup":
        subject = f"Follow-up: {person['research_area'] or person['lab'] or 'your research'}"
        intro = (
            f"I wrote on {person.get('emailed_at') or 'an earlier date'} and have not recorded a reply. "
            f"This follow-up is scheduled {person.get('followup_after_days') or 7} days after that note. "
            "It has not been sent.\n\n"
        )
    else:
        subject = f"Prospective master's student: {person['research_area'] or 'your research'}"
        intro = ""
    body = _template_banner(
        f"Dear Professor {person['professor']},\n\n"
        f"{intro}"
        f"I have been reading about your work{(' in ' + person['research_area']) if person.get('research_area') else ''}"
        f"{(' at ' + person['lab']) if person.get('lab') else ''}"
        f"{(' , ' + person['university']) if person.get('university') else ''}.\n"
        f"{program_line}"
        f"I am {profile.get('name') or 'a prospective student'}"
        f"{(', ' + profile['degree']) if profile.get('degree') else ''}.\n\n"
        "I would be glad to hear whether you are open to a short conversation. I will only send this after I approve the draft.\n\n"
        f"{profile.get('name') or ''}"
    )
    return subject, body


def _professor_prompt(kind: str, person: dict, program: dict | None, profile: dict) -> str:
    label = "a first contact email" if kind == "contact" else "a follow-up email because there is no recorded reply"
    return (
        f"Write {label} to a professor. Do not invent papers, grades, or a meeting. Do not say the email was sent.\n"
        f"Professor: {person['professor']}\nLab: {person.get('lab') or ''}\nUniversity: {person.get('university') or ''}\n"
        f"Research area: {person.get('research_area') or ''}\n"
        f"Follow-up after days: {person.get('followup_after_days') or 7}\n"
        f"Applicant: {profile.get('name') or ''}\nDegree: {profile.get('degree') or ''}\nGPA: {profile.get('gpa')}\n"
        f"Resume excerpt:\n{(profile.get('resume_text') or '')[:3000]}\n"
        f"Program: {json.dumps({key: (program or {}).get(key) for key in ('university', 'department', 'degree', 'research_areas')})}\n"
        'Return JSON: {"subject":"...","body":"..."}'
    )


async def _email_draft(kind: str, template_pair: tuple[str, str], prompt: str) -> tuple[str, str, str]:
    subject, body = template_pair
    settings = db.get_settings()
    if not configured(settings):
        return subject, body, "template"
    try:
        text = await complete(
            settings,
            [
                {
                    "role": "system",
                    "content": (
                        "You draft email the user will review. Return only JSON with subject and body. "
                        "Do not claim the message was sent. Do not invent facts that are not in the prompt."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            timeout=20,
        )
        parsed = _parse_json_object(text)
        drafted_subject = str(parsed.get("subject") or "").strip()
        drafted_body = str(parsed.get("body") or "").strip()
        if drafted_subject and drafted_body:
            return drafted_subject, "DRAFT for your review. Nothing has been sent.\n\n" + drafted_body, "model"
    except (ModelError, json.JSONDecodeError, TimeoutError, OSError, TypeError, ValueError):
        pass
    return subject, body, "template"


def _parse_json_object(text: str) -> dict:
    cleaned = (text or "").strip()
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


def approve_email(email_id: int, subject: str | None = None, body: str | None = None) -> dict:
    email = store.get_email(int(email_id))
    if email is None:
        raise MastersError("That draft is not here.", 404)
    if email.get("sent_at"):
        raise MastersError("This message is already marked sent.")
    fields = {"status": "approved", "approved_at": store.now(), "sent_at": None}
    if subject is not None:
        fields["subject"] = subject
    if body is not None:
        fields["body"] = body
    updated = store.update_email(email["id"], fields)
    _audit("approve_email", "email", updated["id"], "Approved to copy. Not sent.")
    updated["delivery"] = "ready_to_copy" if not smtp_configured() else "approved_not_sent"
    updated["sent"] = False
    return updated


def reject_email(email_id: int) -> dict:
    email = store.get_email(int(email_id))
    if email is None:
        raise MastersError("That draft is not here.", 404)
    if email.get("sent_at"):
        raise MastersError("This message is already marked sent.")
    updated = store.update_email(email["id"], {"status": "rejected", "sent_at": None})
    _audit("reject_email", "email", updated["id"], "Rejected. Not sent.")
    updated["sent"] = False
    return updated


def smtp_configured() -> bool:
    host = os.environ.get("MASTERS_SMTP_HOST", "").strip()
    sender = os.environ.get("MASTERS_SMTP_FROM", "").strip()
    user = os.environ.get("MASTERS_SMTP_USER", "").strip()
    password = os.environ.get("MASTERS_SMTP_PASSWORD", "").strip()
    return bool(host and sender and user and password)


def send_email(email_id: int) -> dict:
    """Send only after approval, and only when SMTP credentials already exist.

    This app does not invent credentials. With no SMTP settings, approval means
    the draft is ready to copy and this function refuses to mark it sent.
    """
    email = store.get_email(int(email_id))
    if email is None:
        raise MastersError("That draft is not here.", 404)
    if email["status"] != "approved" or not email.get("approved_at"):
        raise MastersError("No email is sent without approval.")
    if email.get("sent_at"):
        raise MastersError("This message was already sent.")
    if not smtp_configured():
        raise MastersError(
            "SMTP is not configured. This draft is ready for you to copy and send. Nothing was sent."
        )
    if not email.get("to_address"):
        raise MastersError("This draft has no recipient address. Nothing was sent.")
    raise MastersError(
        "SMTP host is set, but this app will not open a mail connection from the tracker. Copy the approved draft and send it yourself."
    )


def list_emails() -> list[dict]:
    init()
    rows = store.list_emails()
    for row in rows:
        row["sent"] = bool(row.get("sent_at"))
    return rows


def get_email(email_id: int) -> dict:
    email = store.get_email(int(email_id))
    if email is None:
        raise MastersError("That draft is not here.", 404)
    email["sent"] = bool(email.get("sent_at"))
    return email


def create_scholarship(data: dict) -> dict:
    init()
    fields = _clean_scholarship(data)
    created = store.insert_scholarship(fields)
    _sync_scholarship_deadline(created)
    for program_id in data.get("program_ids") or []:
        _require_program(int(program_id))
        store.link_scholarship(created["id"], int(program_id))
    _audit("create_scholarship", "scholarship", created["id"], created["name"])
    return scholarship_view(created["id"])


def update_scholarship(scholarship_id: int, data: dict) -> dict:
    current = store.get_scholarship(int(scholarship_id))
    if current is None:
        raise MastersError("That scholarship is not here.", 404)
    merged = {**current, **data}
    fields = _clean_scholarship(merged)
    store.update_scholarship(current["id"], fields)
    _sync_scholarship_deadline(store.get_scholarship(current["id"]))
    if "program_ids" in data:
        existing = set(store.scholarship_program_ids(current["id"]))
        wanted = {int(program_id) for program_id in data.get("program_ids") or []}
        for program_id in wanted:
            _require_program(program_id)
        for program_id in existing - wanted:
            store.unlink_scholarship(current["id"], program_id)
        for program_id in wanted - existing:
            store.link_scholarship(current["id"], program_id)
    return scholarship_view(current["id"])


def delete_scholarship(scholarship_id: int) -> None:
    current = store.get_scholarship(int(scholarship_id))
    if current is None:
        raise MastersError("That scholarship is not here.", 404)
    store.delete_scholarship(current["id"])


def link_scholarship(scholarship_id: int, program_id: int) -> dict:
    scholarship = store.get_scholarship(int(scholarship_id))
    if scholarship is None:
        raise MastersError("That scholarship is not here.", 404)
    program = _require_program(program_id)
    store.link_scholarship(scholarship["id"], program["id"])
    return scholarship_view(scholarship["id"])


def unlink_scholarship(scholarship_id: int, program_id: int) -> dict:
    scholarship = store.get_scholarship(int(scholarship_id))
    if scholarship is None:
        raise MastersError("That scholarship is not here.", 404)
    store.unlink_scholarship(scholarship["id"], int(program_id))
    return scholarship_view(scholarship["id"])


def _clean_scholarship(data: dict) -> dict:
    name = str(data.get("name") or "").strip()
    if not name:
        raise MastersError("Enter the scholarship name.")
    return {
        "name": name,
        "provider": str(data.get("provider") or "").strip(),
        "amount": str(data.get("amount") or "").strip(),
        "eligibility": str(data.get("eligibility") or "").strip(),
        "deadline": _date_text(data.get("deadline"), "Scholarship deadline"),
        "requires_admission": bool(data.get("requires_admission")),
        "language_requirement": str(data.get("language_requirement") or "").strip(),
        "can_combine_with": str(data.get("can_combine_with") or "").strip(),
    }


def _sync_scholarship_deadline(scholarship: dict) -> None:
    existing = store.find_scholarship_deadline(scholarship["id"])
    if not scholarship.get("deadline"):
        if existing:
            store.delete_deadline_row(existing["id"])
        return
    fields = {
        "type": "scholarship",
        "date": scholarship["deadline"],
        "timezone": "UTC",
        "status": "open",
        "scholarship_id": scholarship["id"],
        "program_id": None,
    }
    if existing:
        store.update_deadline_row(existing["id"], fields)
    else:
        store.insert_deadline(fields)


def scholarship_conflict(scholarship: dict, program_id: int) -> dict:
    application_dates = [
        row["date"]
        for row in store.list_deadline_rows()
        if row.get("program_id") == program_id and row.get("type") == "application"
    ]
    if not scholarship.get("deadline") or not application_dates:
        return {"program_id": program_id, "conflict": False, "reason": ""}
    application = min(application_dates)
    if scholarship["deadline"] < application:
        return {
            "program_id": program_id,
            "conflict": True,
            "reason": (
                f"Scholarship deadline {scholarship['deadline']} is earlier than the application deadline {application}."
            ),
        }
    return {"program_id": program_id, "conflict": False, "reason": ""}


def scholarship_view(scholarship_id: int) -> dict:
    scholarship = store.get_scholarship(int(scholarship_id))
    if scholarship is None:
        raise MastersError("That scholarship is not here.", 404)
    program_ids = store.scholarship_program_ids(scholarship["id"])
    profile = _profile()
    tests = store.list_tests()
    conflicts = [scholarship_conflict(scholarship, program_id) for program_id in program_ids]
    scholarship["program_ids"] = program_ids
    scholarship["conflicts"] = conflicts
    scholarship["conflict"] = any(item["conflict"] for item in conflicts)
    scholarship["eligibility_check"] = evaluate_constraints(
        {
            "gpa_cutoff": _gpa_from_text(scholarship.get("eligibility") or ""),
            "age_limit": None,
            "language_requirement": scholarship.get("language_requirement") or "",
        },
        profile,
        tests,
    )
    return scholarship


def list_scholarships() -> list[dict]:
    init()
    return [scholarship_view(item["id"]) for item in store.list_scholarships()]


def scholarships_for_program(program_id: int) -> list[dict]:
    return [scholarship_view(item["id"]) for item in store.linked_scholarships(program_id)]


def _gpa_from_text(text: str) -> float | None:
    match = re.search(r"GPA[^0-9]{0,16}(\d+(?:\.\d+)?)", text or "", re.I)
    if not match:
        return None
    try:
        return float(match.group(1))
    except ValueError:
        return None


def create_test(data: dict) -> dict:
    init()
    fields = _clean_test(data)
    created = store.insert_test(fields)
    return test_view(created["id"])


def update_test(test_id: int, data: dict) -> dict:
    current = store.get_test(int(test_id))
    if current is None:
        raise MastersError("That test is not here.", 404)
    merged = {
        "test_type": data.get("test_type", current["test_type"]),
        "test_date": data.get("test_date", current.get("test_date")),
        "expiry": data.get("expiry", current.get("expiry")),
        "scores": data.get("scores", current.get("scores")),
        "score": data.get("score"),
        "level": data.get("level"),
    }
    store.update_test(current["id"], _clean_test(merged))
    return test_view(current["id"])


def delete_test(test_id: int) -> None:
    current = store.get_test(int(test_id))
    if current is None:
        raise MastersError("That test is not here.", 404)
    store.delete_test(current["id"])


def _clean_test(data: dict) -> dict:
    test_type = str(data.get("test_type") or "").strip().upper()
    if test_type not in TEST_TYPES:
        raise MastersError("Test type is IELTS, TOEFL, GRE, or JLPT.")
    scores = data.get("scores") if isinstance(data.get("scores"), dict) else {}
    scores = dict(scores)
    if data.get("score") not in (None, ""):
        scores["overall"] = _optional_float(data.get("score"), "Score")
    if data.get("level"):
        scores["level"] = str(data.get("level")).strip().upper()
    if test_type == "JLPT":
        level = _jlpt_level(scores.get("level") or scores.get("overall"))
        if level is None:
            raise MastersError("Enter a JLPT level from N1 to N5.")
        scores = {"level": level}
    else:
        number = None
        for key in ("overall", "total", "score"):
            if scores.get(key) not in (None, ""):
                number = _optional_float(scores.get(key), "Score")
                break
        if number is None:
            raise MastersError("Enter the overall score.")
        scores = {"overall": number}
    return {
        "test_type": test_type,
        "test_date": _date_text(data.get("test_date"), "Test date"),
        "expiry": _date_text(data.get("expiry"), "Expiry"),
        "scores": scores,
    }


def list_tests() -> list[dict]:
    init()
    return [test_view(item["id"]) for item in store.list_tests()]


def test_view(test_id: int) -> dict:
    test = store.get_test(int(test_id))
    if test is None:
        raise MastersError("That test is not here.", 404)
    programs = []
    for program in store.list_program_rows():
        programs.append(test_fit(test, program))
    test["programs"] = programs
    return test


def test_fit(test: dict, program: dict) -> dict:
    parsed = parse_language_requirement(program.get("language_requirement") or "")
    application_dates = [
        row["date"]
        for row in store.list_deadline_rows()
        if row.get("program_id") == program["id"] and row.get("type") == "application"
    ]
    application = min(application_dates) if application_dates else None
    expires_before = bool(test.get("expiry") and application and test["expiry"] < application)
    base = {
        "program_id": program["id"],
        "university": program["university"],
        "degree": program["degree"],
        "application_deadline": application,
        "expires_before_deadline": expires_before,
    }
    if not parsed or parsed.get("test") != test.get("test_type"):
        base.update({"satisfies": False, "reason": "This program does not ask for this test."})
        return base
    if test["test_type"] == "JLPT":
        actual = (test.get("scores") or {}).get("level")
        meets = _language_meets("JLPT", parsed["minimum"], actual)
    else:
        actual = _numeric_score(test)
        meets = actual is not None and _language_meets(test["test_type"], parsed["minimum"], actual)
    if expires_before:
        base.update({
            "satisfies": False,
            "reason": f"This score expires on {test['expiry']}, before the application deadline {application}.",
        })
        return base
    if meets:
        base.update({"satisfies": True, "reason": f"Meets the {parsed['test']} {parsed['minimum']} requirement."})
    else:
        base.update({"satisfies": False, "reason": f"Below the {parsed['test']} {parsed['minimum']} requirement."})
    return base


def save_cost(program_id: int, data: dict) -> dict:
    _require_program(program_id)
    fields = {
        "application_fee": _required_float(data.get("application_fee"), "Application fee"),
        "test_fees": _required_float(data.get("test_fees"), "Test fees"),
        "tuition": _required_float(data.get("tuition"), "Tuition"),
        "living_cost": _required_float(data.get("living_cost"), "Living cost"),
        "funding_amount": _required_float(data.get("funding_amount"), "Funding"),
    }
    store.save_cost(program_id, fields)
    return cost_sheet(program_id)


def cost_sheet(program_id: int) -> dict:
    program = _require_program(program_id)
    cost = store.get_cost(program["id"]) or {
        "program_id": program["id"],
        "application_fee": program.get("application_fee") or 0,
        "test_fees": 0,
        "tuition": 0,
        "living_cost": 0,
        "funding_amount": 0,
    }
    total = (
        float(cost["application_fee"] or 0)
        + float(cost["test_fees"] or 0)
        + float(cost["tuition"] or 0)
        + float(cost["living_cost"] or 0)
    )
    funding = float(cost["funding_amount"] or 0)
    cost = dict(cost)
    cost["total"] = total
    cost["net"] = total - funding
    cost["university"] = program["university"]
    cost["degree"] = program["degree"]
    cost["country"] = program["country"]
    return cost


def compare_costs(program_ids: list[int]) -> dict:
    if not program_ids:
        raise MastersError("Choose at least one program to compare.")
    rows = [cost_sheet(int(program_id)) for program_id in program_ids]
    lowest = min(rows, key=lambda item: item["net"])["program_id"] if rows else None
    return {"programs": rows, "lowest_net_program_id": lowest}


def approval_queue() -> list[dict]:
    init()
    items = []
    for writing in store.list_writings(scope="variant"):
        if writing["status"] != "draft":
            continue
        program = store.get_program_row(writing["program_id"]) if writing.get("program_id") else None
        items.append({
            "queue_type": "writing",
            "id": writing["id"],
            "kind": writing["kind"],
            "title": writing["title"],
            "body": writing["body"],
            "status": writing["status"],
            "program_id": writing.get("program_id"),
            "program_label": program["university"] if program else "",
            "word_count": word_count(writing.get("body") or ""),
            "word_limit": writing.get("word_limit"),
        })
    for email in store.list_emails(status="draft"):
        items.append({
            "queue_type": "email",
            "id": email["id"],
            "kind": email["kind"],
            "title": email["subject"],
            "subject": email["subject"],
            "body": email["body"],
            "status": email["status"],
            "program_id": email.get("program_id"),
            "sent": False,
        })
    for program in store.list_program_rows():
        package = package_view(program["id"])
        if package["blockers"]:
            continue
        if package["status"] == "draft":
            items.append({
                "queue_type": "package",
                "id": program["id"],
                "kind": "package",
                "title": f"{program['university']} — {program['degree']}".strip(" —"),
                "status": package["status"],
                "program_id": program["id"],
                "portal_url": program["portal_url"],
            })
    return items


def dashboard(today: date | None = None) -> dict:
    init()
    today = today or date.today()
    programs = list_programs({}, today)
    deadlines = list_deadlines(today)
    queue = approval_queue()
    reminders = []
    for deadline in deadlines:
        for reminder in deadline["reminders"]:
            reminders.append({
                **reminder,
                "date": deadline["date"],
                "type": deadline["type"],
                "program_label": deadline["program_label"],
                "scholarship_name": deadline["scholarship_name"],
                "band": deadline["band"],
                "sent": False,
            })
    followups = [row for row in list_professors() if row.get("followup_on")]
    blocked = [item for item in programs if item["eligibility"]["blocked"]]
    eligible = [item for item in programs if not item["eligibility"]["blocked"]]
    return {
        "deadlines": deadlines,
        "reminders": reminders,
        "followups": followups,
        "approval_count": len(queue),
        "eligible_count": len(eligible),
        "blocked_count": len(blocked),
        "programs": [
            {
                "id": item["id"],
                "university": item["university"],
                "degree": item["degree"],
                "country": item["country"],
                "status": item["status"],
                "badge": item["eligibility"]["badge"],
                "blocked": item["eligibility"]["blocked"],
                "stale": item["stale"],
                "next_deadline": item["next_deadline"],
                "band": item["band"],
            }
            for item in programs
        ],
    }


def list_audit(limit: int = 50) -> list[dict]:
    init()
    return store.list_audit(limit)


def extract_public_fields(html: str, url: str) -> dict:
    title = _html_title(html)
    plain = html_to_text(html or "", limit=8000)
    university = _guess_university(title) or _guess_university(plain[:400])
    degree = _guess_degree(f"{title}\n{plain[:1500]}")
    department = _guess_department(plain)
    notes = "Extracted from the public page. Every extracted field is unverified until you check it against the source."
    lowered = plain.lower()
    if "sign in" in lowered or "log in" in lowered:
        notes += " The fetch did not use a login or cookies."
    return {
        "university": university or title or url,
        "department": department,
        "degree": degree,
        "country": "",
        "intake": "",
        "application_url": url,
        "portal_url": "",
        "application_fee": None,
        "language_requirement": "",
        "gpa_cutoff": None,
        "age_limit": None,
        "notes": notes,
        "source_url": url,
        "research_areas": "",
        "requirements_text": "",
        "verified": False,
        "verified_at": None,
    }


def _html_title(html: str) -> str:
    match = re.search(r"<title[^>]*>(.*?)</title>", html or "", re.I | re.S)
    if not match:
        return ""
    title = unescape(re.sub(r"\s+", " ", match.group(1))).strip()
    title = re.sub(r"\s*[|\-–—]\s*$", "", title)
    return title[:180]


def _guess_university(text: str) -> str:
    if not text:
        return ""
    parts = re.split(r"\s*[|\-–—]\s*", text)
    for part in parts:
        if re.search(r"university|college|institute|polytechnic", part, re.I):
            return part.strip()[:160]
    return ""


def _guess_degree(text: str) -> str:
    match = re.search(
        r"(Master of [A-Za-z][A-Za-z &]{0,40}|M\.?\s?Sc\.?|MSc|M\.S\.|MS|MA|MBA|Master's)",
        text or "",
        re.I,
    )
    return match.group(0).strip()[:80] if match else ""


def _guess_department(text: str) -> str:
    match = re.search(r"Department of ([A-Za-z][^.\n]{2,80})", text or "", re.I)
    return match.group(0).strip()[:120] if match else ""


async def fetch_public_html(url: str) -> str:
    cleaned = (url or "").strip()
    parsed = urlparse(cleaned)
    if parsed.username or parsed.password:
        raise MastersError("Paste a public page URL. Login details are not used.")
    if not is_public_http(cleaned):
        raise MastersError("Only public http and https pages can be fetched.")
    timeout = httpx.Timeout(15.0, connect=10.0)
    headers = {"User-Agent": FETCH_USER_AGENT, "Accept": "text/html,application/xhtml+xml"}
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, cookies=httpx.Cookies()) as client:
            response = await client.get(cleaned, headers=headers)
            response.raise_for_status()
    except httpx.HTTPError as exc:
        raise MastersError("The public page could not be fetched. Check the URL and try again.") from exc
    final = str(response.url)
    if not is_public_http(final):
        raise MastersError("The page redirected away from a public http address. Nothing was saved.")
    return response.text[:500_000]


async def import_program_url(url: str) -> dict:
    html = await fetch_public_html(url)
    return save_extracted(url, html)


async def save_extracted(url: str, html: str) -> dict:
    fields = extract_public_fields(html, url)
    settings = db.get_settings()
    if configured(settings):
        fields = await _llm_extract(settings, html, fields)
    fields["verified"] = False
    fields["verified_at"] = None
    fields["status"] = "shortlisted"
    program = create_program(fields, apply_template=True)
    # create_program already forces unverified. Re-assert in case of a later edit.
    if program["verified"] or program["verified_at"]:
        store.update_program_row(program["id"], {"verified": 0, "verified_at": None})
        program = get_program(program["id"])
    _audit("import_url", "program", program["id"], "Extracted fields saved as unverified.")
    return program


async def _llm_extract(settings: dict, html: str, fields: dict) -> dict:
    plain = html_to_text(html, limit=6000)
    prompt = (
        "Extract master's program facts that are explicitly on this public page. "
        "If a fact is not on the page, use an empty string or null. Do not guess. "
        "Return JSON with keys university, department, degree, country, intake, "
        "language_requirement, research_areas, requirements_text, gpa_cutoff, age_limit, application_fee. "
        "Do not include verified.\n\n"
        f"Title guess: {fields.get('university')}\n\nPage:\n{plain}"
    )
    try:
        text = await complete(
            settings,
            [
                {"role": "system", "content": "Return only a JSON object of facts copied from the page."},
                {"role": "user", "content": prompt},
            ],
            timeout=20,
        )
        parsed = _parse_json_object(text)
    except (ModelError, json.JSONDecodeError, TimeoutError, OSError, TypeError, ValueError):
        return fields
    for key in ("university", "department", "degree", "country", "intake", "language_requirement", "research_areas", "requirements_text"):
        value = parsed.get(key)
        if isinstance(value, str) and value.strip():
            fields[key] = value.strip()[:500]
    for key in ("gpa_cutoff", "application_fee"):
        if parsed.get(key) not in (None, ""):
            try:
                fields[key] = float(parsed[key])
            except (TypeError, ValueError):
                pass
    if parsed.get("age_limit") not in (None, ""):
        try:
            fields["age_limit"] = int(parsed["age_limit"])
        except (TypeError, ValueError):
            pass
    fields["verified"] = False
    fields["verified_at"] = None
    fields["notes"] = (
        "Extracted from the public page with a model. Every extracted field is unverified until you check it against the source."
    )
    return fields
