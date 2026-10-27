import httpx

from app.sources.common import get_json, make_job

GEOS = ("anywhere", "apac")


def _one(row: dict) -> dict | None:
    geo = row.get("jobGeo") or "Remote"
    if isinstance(geo, list):
        geo = ", ".join(str(item) for item in geo)
    return make_job(
        "jobicy",
        "Jobicy",
        str(row.get("id") or row.get("jobSlug") or ""),
        row.get("jobTitle") or "",
        company=row.get("companyName") or "",
        location=str(geo),
        description=row.get("jobDescription") or row.get("jobExcerpt") or "",
        board_url=str(row.get("url") or ""),
        posted_at=str(row.get("pubDate") or ""),
        is_remote=True,
        location_note="" if str(geo).lower() in {"anywhere", "remote", "worldwide", "apac"} else str(geo),
        html=row.get("jobDescription") or "",
    )


async def fetch(client: httpx.AsyncClient) -> list[dict]:
    found: dict[str, dict] = {}
    errors = 0
    for geo in GEOS:
        url = f"https://jobicy.com/api/jobs?count=50&geo={geo}"
        try:
            payload = await get_json(client, url)
        except (httpx.HTTPError, ValueError):
            errors += 1
            continue
        rows = payload.get("jobs") if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            continue
        for row in rows:
            if isinstance(row, dict):
                job = _one(row)
                if job:
                    found[job["id"]] = job
    if errors == len(GEOS) and not found:
        raise RuntimeError("Jobicy did not return listings")
    return list(found.values())
