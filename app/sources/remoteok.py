import httpx

from app.sources.common import get_json, make_job, salary_range


def _one(row: dict) -> dict | None:
    if not isinstance(row, dict) or not row.get("position"):
        return None
    location = str(row.get("location") or "Remote")
    return make_job(
        "remoteok",
        "Remote OK",
        str(row.get("id") or row.get("slug") or ""),
        row.get("position") or "",
        company=row.get("company") or "",
        location=location,
        salary=salary_range(row.get("salary_min"), row.get("salary_max"), "USD"),
        description=row.get("description") or "",
        board_url=str(row.get("url") or row.get("apply_url") or ""),
        posted_at=str(row.get("date") or ""),
        is_remote=True,
        location_note="" if location.lower() in {"remote", "worldwide", "anywhere", ""} else location,
        html=row.get("description") or "",
    )


async def fetch(client: httpx.AsyncClient) -> list[dict]:
    payload = await get_json(client, "https://remoteok.com/api")
    rows = payload if isinstance(payload, list) else []
    found: dict[str, dict] = {}
    for row in rows:
        job = _one(row)
        if job:
            found[job["id"]] = job
    return list(found.values())
