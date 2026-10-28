"""First Discover batch: hard filters, then about 70% near the profile and 30% different.

Jobs that fail a set constraint are removed before any near/far split.
Empty constraints do not remove jobs. Preference weight is not a hard filter.
"""

from __future__ import annotations

from app.onboarding.filters import passes_constraints

NEAR_FRACTION = 0.7

FAMILY_LABELS = {
    "data_analyst": "data analyst",
    "ml_mlops": "machine learning",
    "business_analyst": "business analyst",
    "operations": "operations",
    "product": "product",
    "it_bank_tech": "it",
}


def job_key(job: dict) -> str:
    for key in ("id", "posting_id", "board_url"):
        value = str(job.get(key) or "").strip()
        if value:
            return value
    return "|".join(
        (
            str(job.get("title") or ""),
            str(job.get("company") or ""),
            str(job.get("location") or ""),
        )
    )


def job_blob(job: dict) -> str:
    skills = " ".join(str(skill) for skill in (job.get("skills") or []))
    family = str(job.get("role_family") or "")
    label = FAMILY_LABELS.get(family, family.replace("_", " "))
    parts = (
        job.get("title"),
        job.get("description"),
        job.get("summary"),
        job.get("company"),
        job.get("industry"),
        job.get("location"),
        skills,
        label,
    )
    return " ".join(str(part or "") for part in parts).casefold()


def _mentions(job: dict, name: str) -> bool:
    target = name.casefold().strip()
    if len(target) < 2:
        return False
    skills = [str(skill).casefold() for skill in job.get("skills") or []]
    if target in skills:
        return True
    family = str(job.get("role_family") or "").replace("_", " ").casefold()
    if target == family or target == FAMILY_LABELS.get(str(job.get("role_family") or ""), ""):
        return True
    return target in job_blob(job)


def _scored(fields: list[dict]) -> tuple[list[dict], list[dict]]:
    prefers = []
    avoids = []
    for field in fields:
        if field.get("source") not in {"inferred", "reflection", "swipe"}:
            continue
        weight = float(field.get("weight") or 0)
        if weight > 0:
            prefers.append(field)
        elif weight < 0:
            avoids.append(field)
    return prefers, avoids


def is_near(job: dict, fields: list[dict]) -> bool:
    prefers, avoids = _scored(fields)
    if any(_mentions(job, field.get("name") or "") for field in avoids):
        return False
    return any(_mentions(job, field.get("name") or "") for field in prefers)


def _take(pool: list[dict], count: int, seen: set[str]) -> list[dict]:
    picked = []
    for job in pool:
        if len(picked) >= count:
            break
        key = job_key(job)
        if key in seen:
            continue
        seen.add(key)
        picked.append(job)
    return picked


def first_batch(jobs: list[dict], constraints: dict | None, fields: list[dict] | None, *, target: int = 20) -> dict:
    """Filter first, then split the survivors about 70/30."""
    size = max(0, int(target))
    eligible = [job for job in jobs if passes_constraints(job, constraints)]
    excluded = len(jobs) - len(eligible)
    near = [job for job in eligible if is_near(job, fields or [])]
    far = [job for job in eligible if not is_near(job, fields or [])]
    near_slots = int(round(size * NEAR_FRACTION)) if size else 0
    far_slots = size - near_slots
    seen: set[str] = set()
    picked = _take(near, near_slots, seen) + _take(far, far_slots, seen)
    if len(picked) < size:
        picked.extend(_take(near + far, size - len(picked), seen))
    picked = picked[:size]
    near_count = sum(1 for job in picked if is_near(job, fields or []))
    return {
        "jobs": picked,
        "near_count": near_count,
        "explore_count": len(picked) - near_count,
        "excluded_count": excluded,
        "target": size,
        "near_fraction": NEAR_FRACTION,
    }


def select_for_discover(postings: list[dict], target: int = 20) -> list[dict] | None:
    """Hand-off used by Discover's first batch. None means Discover should use its own mix."""
    from app.onboarding import store

    latest = store.latest_version()
    if latest is None:
        return None
    filters = store.get_constraints()["filters"]
    fields = (latest.get("data") or {}).get("fields") or []
    return first_batch(postings, filters, fields, target=target)["jobs"]
