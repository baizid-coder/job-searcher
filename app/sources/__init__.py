import asyncio

import httpx

from app.sources import bdjobs, himalayas, jobicy, remoteok, remotive

HEADERS = {
    "User-Agent": "JobFinder/1.0 (personal job search)",
    "Accept": "application/json, text/plain, */*",
}


def _dedupe(jobs: list[dict]) -> list[dict]:
    chosen: dict[tuple[str, str], dict] = {}
    for job in jobs:
        key = (job.get("company", "").lower(), job.get("title", "").lower())
        current = chosen.get(key)
        if current is None or (job.get("company_url") and not current.get("company_url")):
            chosen[key] = job
    return list(chosen.values())


async def collect(keywords: list[str]) -> tuple[list[dict], list[str]]:
    timeout = httpx.Timeout(25.0)
    async with httpx.AsyncClient(timeout=timeout, headers=HEADERS, follow_redirects=True) as client:
        results = await asyncio.gather(
            bdjobs.fetch(client, keywords),
            himalayas.fetch(client),
            jobicy.fetch(client),
            remotive.fetch(client),
            remoteok.fetch(client),
            return_exceptions=True,
        )
    names = ("Bdjobs", "Himalayas", "Jobicy", "Remotive", "Remote OK")
    jobs: list[dict] = []
    warnings: list[str] = []
    for name, result in zip(names, results):
        if isinstance(result, Exception):
            warnings.append(f"{name} did not respond, so those listings are missing from this search.")
        else:
            jobs.extend(result)
    return _dedupe(jobs), warnings
