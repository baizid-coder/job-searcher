"""Discover feed, verdicts, and the profile built from them."""

from __future__ import annotations

from app import db
from app.discover.batch import present_batch, select_postings
from app.discover.classify import BATCH_SIZE, CHIP_LABELS, SUMMARY_EVERY, extract
from app.discover.summary import aggregate, apply_decisions, build_lines, is_stable, next_summary_in
from app.discover import store

VERDICTS = {"yes", "no", "maybe"}


class DiscoverError(Exception):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def _family_counts() -> dict[str, dict]:
    return aggregate(store.verdict_pairs())["families"]


def _confirmed_prefers() -> set[str]:
    chosen = set()
    for decision in store.all_decisions().values():
        if (
            decision["status"] == "confirmed"
            and decision["dimension"] == "role_family"
            and decision["polarity"] == "prefer"
            and decision["value"]
        ):
            chosen.add(decision["value"])
    return chosen


def preferences_stable() -> bool:
    return is_stable(store.list_summaries())


def next_batch(jobs: list[dict], *, target: int = BATCH_SIZE) -> dict:
    judged = store.judged_ids()
    fresh: list[dict] = []
    seen: set[str] = set()
    for job in jobs:
        features = extract(job)
        if features is None:
            continue
        posting_id = features["posting_id"]
        if posting_id in seen:
            continue
        seen.add(posting_id)
        store.upsert_posting(features)
        if posting_id in judged:
            continue
        fresh.append(features)
    selected, thin = select_postings(
        fresh,
        _family_counts(),
        _confirmed_prefers(),
        target=target,
        has_verdicts=bool(judged),
    )
    batch = present_batch(selected, thin)
    count = store.count_verdicts()
    batch.update(
        {
            "ok": True,
            "verdict_count": count,
            "next_summary_in": next_summary_in(count),
            "stable": preferences_stable(),
        }
    )
    return batch


def _validate_chips(verdict: str, chips: list[str], posting: dict) -> list[str]:
    if verdict == "maybe":
        return []
    allowed = set(posting.get("chips") or [])
    cleaned: list[str] = []
    for chip in chips:
        if chip not in CHIP_LABELS or chip not in allowed:
            raise DiscoverError(400, "That reason is not part of this posting.")
        if chip not in cleaned:
            cleaned.append(chip)
    return cleaned


async def record_verdict(posting_id: str, verdict: str, chips: list[str] | None) -> dict:
    choice = (verdict or "").strip().lower()
    if choice not in VERDICTS:
        raise DiscoverError(400, "Choose yes, maybe, or no.")
    posting = store.get_posting(posting_id)
    if posting is None:
        raise DiscoverError(404, "That posting is not in this discover feed.")
    if posting_id in store.judged_ids():
        raise DiscoverError(409, "That posting was already judged.")
    cleaned = _validate_chips(choice, list(chips or []), posting)
    try:
        saved = store.add_verdict(posting_id, choice, cleaned, posting)
    except store.DuplicateVerdict as exc:
        raise DiscoverError(409, "That posting was already judged.") from exc
    summary = await _summarize_if_due()
    return {
        "verdict": saved,
        "summary": _public_summary(summary) if summary else None,
        "verdict_count": store.count_verdicts(),
        "stable": preferences_stable(),
    }


async def _summarize_if_due() -> dict | None:
    count = store.count_verdicts()
    if count == 0 or count % SUMMARY_EVERY != 0:
        return None
    existing = [item for item in store.list_summaries() if item["verdict_count"] == count]
    if existing:
        return existing[0]
    try:
        settings = db.get_settings()
    except Exception:
        settings = {}
    proposed = await build_lines(store.verdict_pairs(), settings)
    lines = apply_decisions(proposed["lines"], store.all_decisions())
    return store.insert_summary(count, proposed["source"], proposed["label"], lines)


def _public_summary(summary: dict | None) -> dict | None:
    if summary is None:
        return None
    lines = [line for line in summary["lines"] if line.get("status") != "rejected"]
    return {
        "id": summary["id"],
        "verdict_count": summary["verdict_count"],
        "source": summary["source"],
        "label": summary["label"],
        "created_at": summary["created_at"],
        "lines": lines,
    }


def profile() -> dict:
    count = store.count_verdicts()
    summaries = store.list_summaries()
    return {
        "ok": True,
        "summary": _public_summary(summaries[-1] if summaries else None),
        "summary_count": len(summaries),
        "stable": is_stable(summaries),
        "verdict_count": count,
        "next_summary_in": next_summary_in(count),
    }


def _line_or_error(key: str) -> tuple[dict, dict]:
    summary = store.latest_summary()
    if summary is None:
        raise DiscoverError(404, "There is no profile summary yet.")
    for line in summary["lines"]:
        if line.get("key") == key and line.get("status") != "rejected":
            return summary, line
    raise DiscoverError(404, "That line is not in the profile.")


def _write_line(summary: dict, line: dict, *, edited: bool | None = None) -> dict:
    lines = []
    for current in summary["lines"]:
        if current.get("key") == line["key"]:
            lines.append(line)
        elif current.get("status") != "rejected":
            lines.append(current)
    store.save_decision(line, edited=edited)
    store.update_summary_lines(summary["id"], lines)
    return line


def confirm_line(key: str) -> dict:
    summary, line = _line_or_error(key)
    if line.get("locked"):
        raise DiscoverError(409, "That line is locked. Unlock it before changing it.")
    updated = dict(line)
    updated["status"] = "confirmed"
    updated["locked"] = True
    return _write_line(summary, updated, edited=False if not _edited(key) else True)


def _edited(key: str) -> bool:
    decision = store.get_decision(key)
    return bool(decision and decision["edited"])


def reject_line(key: str) -> dict:
    summary, line = _line_or_error(key)
    if line.get("locked"):
        raise DiscoverError(409, "That line is locked. Unlock it before changing it.")
    updated = dict(line)
    updated["status"] = "rejected"
    updated["locked"] = False
    store.save_decision(updated, edited=_edited(key))
    remaining = [item for item in summary["lines"] if item.get("key") != key]
    store.update_summary_lines(summary["id"], remaining)
    return {"key": key, "status": "rejected", "locked": False}


def edit_line(key: str, text: str) -> dict:
    cleaned = (text or "").strip()
    if not cleaned:
        raise DiscoverError(400, "Write the line you want to keep.")
    summary, line = _line_or_error(key)
    updated = dict(line)
    updated["text"] = cleaned
    updated["status"] = "confirmed"
    updated["locked"] = True
    return _write_line(summary, updated, edited=True)


def unlock_line(key: str) -> dict:
    summary, line = _line_or_error(key)
    if line.get("status") != "confirmed":
        raise DiscoverError(400, "Only a confirmed line can be unlocked.")
    updated = dict(line)
    updated["locked"] = False
    return _write_line(summary, updated, edited=_edited(key))
