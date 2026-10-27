import asyncio
import html
import json
import re
from urllib.parse import parse_qs, urljoin, urlparse

import httpx

from app.sources.common import make_job
from app.textutil import ATS_HOSTS, html_to_text, is_public_http

HEADERS = {
    "User-Agent": "JobFinder/1.0 (personal job search; public pages only)",
    "Accept": "text/html,application/xhtml+xml",
}

MAX_URLS = 10
EXTERNAL_NOTE = "The external apply portal was not public."
PASTED_REASON = "Pasted from LinkedIn"

_JOB_URL = re.compile(
    r"https?://[^\s<>\"']+"
    r"|(?:(?:www|[\w-]+)\.)?linkedin\.com/jobs/[^\s<>\"']+"
    r"|lnkd\.in/[A-Za-z0-9_-]+",
    re.I,
)
_APPLY_KEYS = ("companyApplyUrl", "externalApplyUrl", "offsiteApplyUrl")
_SECRET_TEXT = re.compile(r"\bli_at\b|jsessionid|linkedin password", re.I)


def looks_like_secret(text: str) -> bool:
    return bool(_SECRET_TEXT.search(text or ""))


def job_id_from_url(url: str) -> str:
    parsed = urlparse(url)
    current = parse_qs(parsed.query).get("currentJobId") or []
    if current and str(current[0]).isdigit():
        return str(current[0])
    match = re.search(r"/jobs/view/(?:[a-z0-9_-]+-)?(\d{5,})", parsed.path, re.I)
    if match:
        return match.group(1)
    return ""


def _host_is_linkedin(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    if host == "lnkd.in" or host.endswith(".lnkd.in"):
        return True
    return host == "linkedin.com" or host.endswith(".linkedin.com")


def _is_job_url(url: str) -> bool:
    if not is_public_http(url) or not _host_is_linkedin(url):
        return False
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if host == "lnkd.in" or host.endswith(".lnkd.in"):
        return True
    path = parsed.path.lower()
    if "/jobs/view/" in path or "/jobs/collections/" in path or path.startswith("/jobs/search"):
        return True
    return bool(parse_qs(parsed.query).get("currentJobId"))


def canonical_job_url(url: str) -> str:
    job_id = job_id_from_url(url)
    if job_id:
        return f"https://www.linkedin.com/jobs/view/{job_id}"
    return url.split("#")[0]


def extract_urls(text: str) -> tuple[list[str], list[str]]:
    found: list[str] = []
    seen: set[str] = set()
    warnings: list[str] = []
    overflow = False
    for match in _JOB_URL.finditer(text or ""):
        raw = match.group(0).rstrip(").,]>\"'")
        if not raw.lower().startswith("http"):
            raw = "https://" + raw
        if not _is_job_url(raw):
            continue
        url = canonical_job_url(raw)
        key = job_id_from_url(url) or url.rstrip("/").lower()
        if key in seen:
            continue
        seen.add(key)
        if len(found) >= MAX_URLS:
            overflow = True
            continue
        found.append(url)
    if overflow:
        warnings.append(f"Only the first {MAX_URLS} LinkedIn links are fetched.")
    return found, warnings


def _off_linkedin(url: str) -> bool:
    if not is_public_http(url):
        return False
    return not _host_is_linkedin(url)


def _unescape_url(value: str) -> str:
    return html.unescape(value.replace("\\/", "/").replace("\\u0026", "&")).strip()


def _json_ld_jobs(page: str) -> list[dict]:
    found: list[dict] = []
    for match in re.finditer(
        r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        page,
        flags=re.I | re.S,
    ):
        try:
            payload = json.loads(match.group(1).strip())
        except json.JSONDecodeError:
            continue
        stack = payload if isinstance(payload, list) else [payload]
        index = 0
        while index < len(stack):
            item = stack[index]
            index += 1
            if not isinstance(item, dict):
                continue
            graph = item.get("@graph")
            if isinstance(graph, list):
                stack.extend(entry for entry in graph if isinstance(entry, dict))
            kind = item.get("@type") or ""
            kinds = kind if isinstance(kind, list) else [kind]
            if any(str(name).lower() == "jobposting" for name in kinds):
                found.append(item)
    return found


def _blocked(page: str, status: int, has_posting: bool) -> bool:
    if has_posting and status < 400:
        return False
    if status in {401, 403, 429, 999}:
        return True
    lowered = page.lower()
    if "authwall" in lowered or "security verification" in lowered:
        return True
    if not has_posting and re.search(r"sign in to (?:view|see)|join linkedin to", lowered):
        return True
    return False


def _location(posting: dict) -> str:
    loc = posting.get("jobLocation")
    if isinstance(loc, list):
        loc = loc[0] if loc else {}
    if isinstance(loc, dict):
        address = loc.get("address") or {}
        if isinstance(address, dict):
            parts = [
                address.get("addressLocality"),
                address.get("addressRegion"),
                address.get("addressCountry"),
            ]
            text = ", ".join(str(part) for part in parts if part)
            if text:
                return text
        if loc.get("name"):
            return str(loc["name"])
    if str(posting.get("jobLocationType") or "").upper() == "TELECOMMUTE":
        return "Remote"
    return ""


def _company_name(posting: dict) -> str:
    org = posting.get("hiringOrganization") or {}
    if isinstance(org, dict):
        return str(org.get("name") or "")
    if isinstance(org, str):
        return org
    return ""


def _clean_title(title: str) -> str:
    title = html_to_text(html.unescape(title or ""), limit=180)
    title = re.sub(r"\s*[|–—-]\s*LinkedIn(?:\s+Jobs)?\s*$", "", title, flags=re.I).strip()
    if not title or title.lower() in {"sign in", "linkedin", "login", "join linkedin"}:
        return ""
    if title.lower().startswith("sign in"):
        return ""
    return title


def _fragment(page: str, pattern: str, limit: int) -> str:
    match = re.search(pattern, page, flags=re.I | re.S)
    if not match:
        return ""
    return html_to_text(match.group(1), limit=limit)


def _top_card(page: str) -> tuple[str, str, str, str]:
    title = _clean_title(_fragment(page, r"<h1[^>]*>(.*?)</h1>", 180))
    company = _fragment(
        page,
        r'class="[^"]*topcard__org-name-link[^"]*"[^>]*>(.*?)</a>',
        160,
    )
    location = _fragment(
        page,
        r'class="[^"]*topcard__flavor--bullet[^"]*"[^>]*>(.*?)</span>',
        160,
    )
    description = _fragment(
        page,
        r'class="[^"]*show-more-less-html__markup[^"]*"[^>]*>(.*?)</div>',
        8000,
    )
    return title, company, location, description


def _title_from_html(page: str) -> str:
    patterns = (
        r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:title["\']',
        r"<title>([^<]+)</title>",
    )
    for pattern in patterns:
        match = re.search(pattern, page, flags=re.I)
        if not match:
            continue
        title = _clean_title(match.group(1))
        if title:
            return title
    return ""


def external_apply_url(page: str, page_url: str) -> str:
    for key in _APPLY_KEYS:
        for match in re.finditer(rf'"{key}"\s*:\s*"([^"]+)"', page):
            candidate = _unescape_url(match.group(1))
            if _off_linkedin(candidate):
                return candidate
    for match in re.finditer(r'"applyUrl"\s*:\s*"([^"]+)"', page):
        candidate = _unescape_url(match.group(1))
        if _off_linkedin(candidate):
            return candidate
    for match in re.finditer(
        r'<a\b[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>',
        page,
        flags=re.I | re.S,
    ):
        href = _unescape_url(urljoin(page_url, html.unescape(match.group(1))))
        label = html_to_text(match.group(2), limit=200).lower()
        if not _off_linkedin(href):
            continue
        host = (urlparse(href).hostname or "").lower()
        if any(ats in host for ats in ATS_HOSTS):
            return href
        if re.search(r"apply|career|company website", label):
            return href
    for match in re.finditer(r'href=["\'](https?://[^"\']+)["\']', page, flags=re.I):
        href = _unescape_url(match.group(1))
        if not _off_linkedin(href):
            continue
        host = (urlparse(href).hostname or "").lower()
        if any(ats in host for ats in ATS_HOSTS):
            return href
    return ""


def lead_from_html(page: str, page_url: str, status: int = 200) -> dict | None:
    job_id = job_id_from_url(page_url)
    board = f"https://www.linkedin.com/jobs/view/{job_id}" if job_id else page_url
    postings = _json_ld_jobs(page)
    posting = postings[0] if postings else {}
    blocked = _blocked(page, status, bool(posting))
    external = "" if blocked else external_apply_url(page, page_url or board)
    card_title, card_company, card_location, card_description = _top_card(page)
    title = _clean_title(str(posting.get("title") or "")) or card_title or _title_from_html(page)
    if not title:
        title = f"LinkedIn job {job_id}" if job_id else "LinkedIn job"
    company = _company_name(posting) or card_company
    location = _location(posting) or card_location
    description = html_to_text(str(posting.get("description") or "")) or card_description
    if not external:
        description = f"{EXTERNAL_NOTE}\n\n{description}".strip() if description else EXTERNAL_NOTE
    reasons = [PASTED_REASON]
    if not external:
        reasons.append(EXTERNAL_NOTE)
    job = make_job(
        "linkedin",
        "LinkedIn",
        job_id or page_url,
        title,
        company=company,
        location=location,
        description=description,
        board_url=board,
        company_url=external,
        is_remote="remote" in f"{_location(posting)} {description}".lower(),
        location_note="" if external else EXTERNAL_NOTE,
    )
    if job is None:
        return None
    job["reasons"] = reasons
    job["score"] = 0
    job["official_url"] = external or board
    job["posting_label"] = "Company page" if external else "LinkedIn page"
    job["posting_text"] = description
    return job


async def _read(client: httpx.AsyncClient, url: str) -> tuple[str, str, int, str]:
    current = url
    for _ in range(5):
        response = await client.get(current)
        status = response.status_code
        if status in {301, 302, 303, 307, 308}:
            location = response.headers.get("location") or ""
            nxt = urljoin(current, location)
            if not is_public_http(nxt) or not _host_is_linkedin(nxt):
                external = nxt if _off_linkedin(nxt) else ""
                return "", current, status, external
            current = nxt
            continue
        return response.text[:400_000], str(response.url), status, ""
    return "", current, 0, ""


async def fetch_jobs(
    urls: list[str],
    client: httpx.AsyncClient | None = None,
) -> tuple[list[dict], list[str]]:
    warnings: list[str] = []
    jobs: list[dict] = []
    owns_client = client is None
    if client is None:
        client = httpx.AsyncClient(timeout=httpx.Timeout(15.0), headers=HEADERS, follow_redirects=False)
    try:
        for index, url in enumerate(urls):
            if index and owns_client:
                await asyncio.sleep(0.4)
            try:
                page, final_url, status, redirected = await _read(client, url)
            except httpx.HTTPError:
                warnings.append(f"{url} could not be fetched.")
                continue
            final_url = final_url or url
            if page and "/jobs/" not in urlparse(final_url).path.lower() and "currentJobId" not in final_url:
                if not _json_ld_jobs(page):
                    warnings.append(f"{url} did not open a public LinkedIn job page.")
                    continue
            job = lead_from_html(page, final_url if page else url, status)
            if job is None:
                warnings.append(f"{url} did not include a job title.")
                continue
            if redirected and not job.get("company_url"):
                job["company_url"] = redirected
                job["official_url"] = redirected
                job["posting_label"] = "Company page"
                job["location_note"] = ""
                job["reasons"] = [PASTED_REASON]
                job["description"] = ""
                job["posting_text"] = ""
            jobs.append(job)
            if not job.get("company_url"):
                warnings.append(f"{job['title']}: {EXTERNAL_NOTE}")
    finally:
        if owns_client:
            await client.aclose()
    return jobs, warnings
