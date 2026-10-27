from urllib.parse import urlencode

import httpx

from app.sources.common import get_json, make_job

ENDPOINT = "https://api.bdjobs.com/Jobs/api/JobSearch/GetJobSearch"


def _rows(payload: dict) -> list[dict]:
    rows = []
    for key in ("premiumData", "data"):
        value = payload.get(key) or []
        if isinstance(value, list):
            rows.extend(item for item in value if isinstance(item, dict))
    return rows


def _one(row: dict, keyword: str = "") -> dict | None:
    job_id = str(row.get("Jobid") or row.get("jobId") or "").strip()
    title = row.get("jobTitle") or row.get("JobTitle") or ""
    if not job_id or not title:
        return None
    workplace = str(row.get("WorkPlace") or "")
    context = row.get("jobContext") or ""
    details = row.get("jobDescription") or ""
    experience = row.get("experience") or ""
    description = "\n".join(part for part in (context, experience, details) if part)
    salary = str(row.get("Salary") or "").strip()
    if salary in {"--", "-", "Negotiable"}:
        salary = ""
    location = str(row.get("location") or "").strip()
    if location and "bangladesh" not in location.lower():
        location = f"{location}, Bangladesh"
    elif not location:
        location = "Bangladesh"
    job = make_job(
        "bdjobs",
        "Bdjobs",
        job_id,
        title,
        company=row.get("companyName") or "",
        location=location,
        salary=salary,
        description=description,
        board_url=f"https://www.bdjobs.com/jobdetails.asp?id={job_id}",
        posted_at=str(row.get("publishDate") or ""),
        is_remote="remote" in workplace.lower() or "home" in workplace.lower(),
        html=context,
    )
    if job and keyword:
        job["queried_title"] = keyword
    return job


async def fetch(client: httpx.AsyncClient, keywords: list[str]) -> list[dict]:
    chosen: list[str] = []
    seen: set[str] = set()
    for keyword in keywords:
        cleaned = str(keyword).strip()
        if not cleaned or cleaned.lower() in seen:
            continue
        seen.add(cleaned.lower())
        chosen.append(cleaned)
        if len(chosen) == 6:
            break
    if not chosen:
        return []
    found: dict[str, dict] = {}
    errors = 0
    for keyword in chosen:
        url = f"{ENDPOINT}?{urlencode({'Keyword': keyword})}"
        payload = None
        for _attempt in range(2):
            try:
                payload = await get_json(client, url, timeout=40)
                break
            except (httpx.HTTPError, ValueError):
                payload = None
        if payload is None:
            errors += 1
            continue
        for row in _rows(payload if isinstance(payload, dict) else {}):
            job = _one(row, keyword)
            if job:
                found[job["id"]] = job
    if errors and not found:
        raise RuntimeError("Bdjobs did not return listings")
    return list(found.values())
