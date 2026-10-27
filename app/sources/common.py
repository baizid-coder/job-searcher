import hashlib
import json
import re

import httpx

from app import db
from app.textutil import find_company_url, html_to_text

HEADERS = {
    "User-Agent": "JobFinder/1.0 (personal job search)",
    "Accept": "application/json, text/plain, */*",
}


def make_id(source_key: str, external: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9_-]", "", str(external))[:80]
    if not safe:
        safe = hashlib.sha1(str(external).encode()).hexdigest()[:16]
    return f"{source_key}-{safe}"


def make_job(
    source_key: str,
    source: str,
    external: str,
    title: str,
    company: str = "",
    location: str = "",
    salary: str = "",
    description: str = "",
    board_url: str = "",
    company_url: str = "",
    posted_at: str = "",
    is_remote: bool = False,
    location_note: str = "",
    html: str = "",
) -> dict | None:
    cleaned_title = html_to_text(title, limit=180)
    if not cleaned_title:
        return None
    body = html_to_text(description or html)
    found = company_url or find_company_url(html or description or "")
    return {
        "id": make_id(source_key, external),
        "source": source,
        "title": cleaned_title,
        "company": html_to_text(company, limit=160),
        "location": location.strip(),
        "salary": salary.strip(),
        "description": body,
        "board_url": board_url.strip(),
        "company_url": found.strip(),
        "posted_at": posted_at.strip(),
        "is_remote": is_remote,
        "location_note": location_note.strip(),
    }


def salary_range(minimum, maximum, currency: str = "", period: str = "") -> str:
    def number(value):
        if value in (None, "", 0):
            return ""
        try:
            amount = int(float(value))
        except (TypeError, ValueError):
            return str(value)
        return f"{amount:,}"

    low = number(minimum)
    high = number(maximum)
    if not low and not high:
        return ""
    amount = low if low == high or not high else (high if not low else f"{low}-{high}")
    parts = [part for part in (currency, amount) if part]
    text = " ".join(parts)
    if period:
        text = f"{text} / {period}"
    return text


async def get_json(client: httpx.AsyncClient, url: str, timeout: float = 25):
    cached = db.cache_get(url)
    if cached:
        return json.loads(cached)
    response = await client.get(url, headers=HEADERS, timeout=timeout)
    response.raise_for_status()
    db.cache_set(url, response.text)
    return response.json()
