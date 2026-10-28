import asyncio
import logging
from datetime import datetime, timedelta, timezone

from app import db
from app.match import query_keywords, rank_jobs, search_block_reason, select_visible
from app.sources import collect

log = logging.getLogger("uvicorn.error")

INTERVAL_HOURS = 8
NEW_PER_REFRESH = 25
FAILURE_RETRY_SECONDS = 15 * 60
IDLE_POLL_SECONDS = 60
DISABLED_POLL_SECONDS = 30
START_DELAY_SECONDS = 5

_lock = asyncio.Lock()
_retry_after: datetime | None = None


def seconds_until_due(last_feed_at: str, now: datetime) -> float:
    if not last_feed_at:
        return 0.0
    try:
        last = datetime.fromisoformat(last_feed_at)
    except ValueError:
        return 0.0
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    return max(0.0, (last + timedelta(hours=INTERVAL_HOURS) - now).total_seconds())


def next_delay(
    auto_refresh: bool,
    last_feed_at: str,
    now: datetime,
    retry_after: datetime | None = None,
) -> float | None:
    """Seconds to wait before looking again. None means a refresh is due now."""
    if not auto_refresh:
        return float(DISABLED_POLL_SECONDS)
    if retry_after is not None and now < retry_after:
        return min(float(IDLE_POLL_SECONDS), (retry_after - now).total_seconds())
    remaining = seconds_until_due(last_feed_at, now)
    if remaining > 0:
        return min(float(IDLE_POLL_SECONDS), remaining)
    return None


def public_feed_status() -> dict:
    stored = db.get_feed_settings()
    last = stored["last_feed_at"]
    nxt = ""
    if stored["auto_refresh"] and last:
        try:
            parsed = datetime.fromisoformat(last)
        except ValueError:
            parsed = None
        if parsed is not None:
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            nxt = (parsed + timedelta(hours=INTERVAL_HOURS)).isoformat()
    return {
        "auto_refresh": stored["auto_refresh"],
        "interval_hours": INTERVAL_HOURS,
        "last_refresh_at": last,
        "next_refresh_at": nxt,
        "last_added": stored["last_feed_added"],
    }


def select_for_store(ranked: list[dict], existing_ids: set[str], limit: int = NEW_PER_REFRESH) -> list[dict]:
    """Refresh listings already stored, and keep at most `limit` new ones.

    The new rows are the highest scored matches that are not in the database yet.
    """
    updates: list[dict] = []
    fresh: list[dict] = []
    seen: set[str] = set()
    for job in ranked:
        job_id = job["id"]
        if job_id in seen:
            continue
        seen.add(job_id)
        if job_id in existing_ids:
            updates.append(job)
        else:
            fresh.append(job)
    return updates + fresh[:limit]


def _result(**extra) -> dict:
    payload = {
        "deferred": False,
        "skipped": False,
        "matched": 0,
        "added": 0,
        "warnings": [],
    }
    payload.update(extra)
    return payload


async def _collect_and_store(profile: dict) -> dict:
    reason = search_block_reason(profile)
    if reason:
        return _result(skipped=True, reason=reason)
    jobs, warnings = await collect(query_keywords(profile))
    visible = select_visible(rank_jobs(jobs, profile))
    warnings = list(warnings)
    if warnings and not visible:
        log.warning("Job feeds were not updated because a board did not respond: %s", " ".join(warnings))
        return _result(deferred=True, warnings=warnings)
    if not visible and not warnings:
        if jobs:
            warnings.append("No listings were left after your excluded terms and work-mode limits.")
        else:
            warnings.append("The job boards returned no listings.")
    # The wider board chooses which listings this check may add. Leads already stored stay.
    chosen = select_for_store(visible, db.job_ids(), limit=len(visible))
    added = db.store_search(chosen)
    db.mark_feed_refreshed(added)
    log.info("Job feeds updated. %s matched, %s new listings kept.", len(visible), added)
    return _result(matched=len(visible), added=added, warnings=warnings)


async def collect_and_store(profile: dict) -> dict:
    async with _lock:
        return await _collect_and_store(profile)


async def refresh_saved_profile() -> dict:
    if _lock.locked():
        return _result(deferred=True, reason="busy")
    profile = db.get_profile()
    reason = search_block_reason(profile)
    if reason:
        return _result(skipped=True, reason=reason)
    return await collect_and_store(profile)


async def tick() -> float:
    global _retry_after
    settings = db.get_feed_settings()
    now = datetime.now(timezone.utc)
    delay = next_delay(settings["auto_refresh"], settings["last_feed_at"], now, _retry_after)
    if delay is not None:
        return delay
    result = await refresh_saved_profile()
    if result.get("deferred"):
        _retry_after = datetime.now(timezone.utc) + timedelta(seconds=FAILURE_RETRY_SECONDS)
        return float(FAILURE_RETRY_SECONDS)
    _retry_after = None
    return float(IDLE_POLL_SECONDS)


async def _wait(stop: asyncio.Event, seconds: float) -> bool:
    try:
        await asyncio.wait_for(stop.wait(), timeout=max(0.0, seconds))
    except asyncio.TimeoutError:
        return False
    return True


async def feed_loop(stop: asyncio.Event) -> None:
    """Check the saved requirements on a timer and add listings that are not stored yet."""
    if await _wait(stop, START_DELAY_SECONDS):
        return
    while not stop.is_set():
        try:
            delay = await tick()
        except Exception:
            log.exception("Job feed refresh failed")
            delay = float(FAILURE_RETRY_SECONDS)
        if await _wait(stop, delay):
            return
