from urllib.parse import urlparse

import httpx

from app.match import _keywords
from app.textutil import find_company_url, html_to_text, is_public_http, looks_unreadable

BROWSER = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml",
}


async def _read(client: httpx.AsyncClient, url: str) -> tuple[str, str]:
    if not is_public_http(url):
        return "", ""
    response = await client.get(url, headers=BROWSER)
    response.raise_for_status()
    content_type = response.headers.get("content-type", "")
    if content_type and "html" not in content_type and "text/" not in content_type:
        return "", ""
    html = response.text[:400_000]
    return html_to_text(html, limit=12000), html


def _readable(text: str) -> bool:
    return bool(text) and not looks_unreadable(text)


def _off_linkedin(url: str) -> bool:
    if not is_public_http(url):
        return False
    host = (urlparse(url).hostname or "").lower()
    if host == "lnkd.in" or host.endswith(".lnkd.in"):
        return False
    if host == "linkedin.com" or host.endswith(".linkedin.com"):
        return False
    return True


def _relevant(title: str, text: str) -> bool:
    if not _readable(text):
        return False
    words = _keywords(title)
    if not words:
        return True
    lowered = text.lower()
    return any(word in lowered for word in words)


async def load_posting(job: dict) -> dict:
    board_url = job.get("board_url") or ""
    company_url = job.get("company_url") or ""
    description = job.get("description") or ""
    source = job.get("source") or "Job board"
    title = job.get("title") or ""
    text = ""
    official_url = board_url or company_url
    found_company = False
    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            if company_url:
                text, html = await _read(client, company_url)
                if _relevant(title, text):
                    found_company = True
                    official_url = company_url
                else:
                    hopped = find_company_url(html)
                    text = ""
                    if hopped and hopped != company_url:
                        hopped_text, _html = await _read(client, hopped)
                        if _relevant(title, hopped_text):
                            company_url = hopped
                            official_url = hopped
                            text = hopped_text
                            found_company = True
            if not found_company and board_url:
                board_text, board_html = await _read(client, board_url)
                hopped = find_company_url(board_html)
                if hopped and hopped not in {board_url, company_url}:
                    hopped_text, _html = await _read(client, hopped)
                    if _relevant(title, hopped_text):
                        company_url = hopped
                        official_url = hopped
                        text = hopped_text
                        found_company = True
                if not found_company and _relevant(title, board_text):
                    text = board_text
                    official_url = board_url
                    company_url = ""
    except httpx.HTTPError:
        text = ""

    if found_company:
        label = "Company page"
    elif _relevant(title, text):
        label = f"{source} page"
        official_url = board_url or official_url
    else:
        label = f"{source} page"
        official_url = board_url or company_url
        note = "The company page could not be read from here. This is the description from the job feed."
        text = f"{note}\n\n{description}".strip() if description else note
    if source == "LinkedIn" and _off_linkedin(job.get("company_url") or ""):
        company_url = job.get("company_url") or company_url
        official_url = company_url
        label = "Company page"
    return {
        "posting_text": text,
        "posting_label": label,
        "official_url": official_url,
        "company_url": company_url,
    }
