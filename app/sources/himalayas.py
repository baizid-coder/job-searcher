from urllib.parse import quote

import httpx

from app.sources.common import get_json, make_job, salary_range

BASE = "https://himalayas.app/jobs/api?limit=20"


def _one(row: dict) -> dict | None:
    guid = str(row.get("guid") or row.get("applicationLink") or row.get("title") or "")
    restrictions = row.get("locationRestrictions") or []
    if not isinstance(restrictions, list):
        restrictions = [str(restrictions)]
    location = ", ".join(str(item) for item in restrictions if item) or "Remote"
    note = location if location not in {"Remote", "Worldwide", "Anywhere"} else ""
    slug = guid.rstrip("/").split("/")[-1] or guid
    return make_job(
        "himalayas",
        "Himalayas",
        slug,
        row.get("title") or "",
        company=row.get("companyName") or "",
        location=location,
        salary=salary_range(row.get("minSalary"), row.get("maxSalary"), row.get("currency") or "", row.get("salaryPeriod") or ""),
        description=row.get("description") or row.get("excerpt") or "",
        board_url=str(row.get("applicationLink") or row.get("guid") or ""),
        posted_at=str(row.get("pubDate") or ""),
        is_remote=True,
        location_note=note,
        html=row.get("description") or "",
    )


async def fetch(client: httpx.AsyncClient) -> list[dict]:
    found: dict[str, dict] = {}
    cursor = ""
    for _ in range(5):
        url = BASE if not cursor else f"{BASE}&cursor={quote(cursor, safe='')}"
        payload = await get_json(client, url)
        rows = payload.get("jobs") if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            break
        for row in rows:
            if isinstance(row, dict):
                job = _one(row)
                if job:
                    found[job["id"]] = job
        cursor = str(payload.get("nextCursor") or "")
        if not cursor:
            break
    return list(found.values())
