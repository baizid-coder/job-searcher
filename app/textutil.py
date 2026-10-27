import re
from html.parser import HTMLParser
from urllib.parse import urlparse

ATS_HOSTS = (
    "greenhouse.io",
    "lever.co",
    "ashbyhq.com",
    "myworkdayjobs.com",
    "smartrecruiters.com",
    "workable.com",
    "recruitee.com",
    "bamboohr.com",
    "jobvite.com",
    "icims.com",
    "taleo.net",
)

BOARD_HOSTS = (
    "bdjobs.com",
    "himalayas.app",
    "jobicy.com",
    "remoteok.com",
    "remotive.com",
)


class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.links: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "noscript"}:
            self._skip += 1
        if tag == "a":
            href = dict(attrs).get("href")
            if href:
                self.links.append(href)
        if tag in {"p", "br", "li", "div", "h1", "h2", "h3", "tr", "section"}:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in {"script", "style", "noscript"} and self._skip:
            self._skip -= 1
        if tag in {"p", "li", "h1", "h2", "h3", "tr"}:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def html_to_text(value: str, limit: int = 8000) -> str:
    if not value:
        return ""
    if "<" not in value and "&" not in value:
        text = value
    else:
        parser = _TextExtractor()
        try:
            parser.feed(value)
            parser.close()
        except Exception:
            text = re.sub(r"<[^>]+>", " ", value)
        else:
            text = "".join(parser.parts)
    text = text.replace("\xa0", " ")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip()[:limit]


def html_links(value: str) -> list[str]:
    if not value or "<" not in value:
        return []
    parser = _TextExtractor()
    try:
        parser.feed(value)
        parser.close()
    except Exception:
        return re.findall(r'href=["\'](https?://[^"\']+)', value, flags=re.I)
    return parser.links


def contains_term(text: str, term: str) -> bool:
    cleaned = term.strip().lower()
    if not cleaned:
        return False
    haystack = text.lower()
    if " " in cleaned:
        return cleaned in haystack
    pattern = rf"(?<![a-z0-9+#.]){re.escape(cleaned)}(?![a-z0-9+#])"
    return re.search(pattern, haystack) is not None


def is_public_http(url: str) -> bool:
    parsed = urlparse(url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False
    host = parsed.hostname.lower()
    if host in {"localhost", "127.0.0.1", "0.0.0.0", "::1"} or host.endswith(".local"):
        return False
    return True


def find_company_url(html: str) -> str:
    ats_hits: list[str] = []
    career_hits: list[str] = []
    for href in html_links(html):
        if not is_public_http(href):
            continue
        host = urlparse(href).hostname.lower()
        if any(board in host for board in BOARD_HOSTS):
            continue
        if any(ats in host for ats in ATS_HOSTS):
            ats_hits.append(href)
        elif re.search(r"/jobs?/\d|lever\.co|greenhouse|ashby", href, flags=re.I):
            career_hits.append(href)
    if ats_hits:
        return ats_hits[0]
    if career_hits:
        return career_hits[0]
    return ""


def looks_unreadable(text: str) -> bool:
    lowered = text.lower()
    if len(text.strip()) < 80:
        return True
    blocked = ("enable javascript", "just a moment", "access denied", "captcha", "are you a human")
    return any(hint in lowered for hint in blocked) and len(text) < 1800
