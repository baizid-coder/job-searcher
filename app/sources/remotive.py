import httpx

from app.sources.common import get_json, make_job


def _one(row: dict) -> dict | None:
    location = str(row.get("candidate_required_location") or "Remote")
    return make_job(
        "remotive",
        "Remotive",
        str(row.get("id") or ""),
        row.get("title") or "",
        company=row.get("company_name") or "",
        location=location,
        salary=str(row.get("salary") or ""),
        description=row.get("description") or "",
        board_url=str(row.get("url") or ""),
        posted_at=str(row.get("publication_date") or ""),
        is_remote=True,
        location_note="" if location.lower() in {"worldwide", "anywhere", "remote", ""} else location,
        html=row.get("description") or "",
    )


async def fetch(client: httpx.AsyncClient) -> list[dict]:
    payload = await get_json(client, "https://remotive.com/api/remote-jobs?limit=100")
    rows = payload.get("jobs") if isinstance(payload, dict) else None
    found: dict[str, dict] = {}
    if isinstance(rows, list):
        for row in rows:
            if isinstance(row, dict):
                job = _one(row)
                if job:
                    found[job["id"]] = job
    return list(found.values())
