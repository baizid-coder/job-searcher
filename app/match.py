import re

from app.textutil import contains_term

GENERIC_TITLE_WORDS = {
    "developer", "engineer", "manager", "specialist", "officer", "executive",
    "assistant", "senior", "junior", "lead", "intern", "associate", "consultant",
    "analyst", "coordinator", "representative",
}

SHORT_KEEP = {"ai", "bi", "qa", "ui", "ux", "hr", "ml", "vs", "db"}

# A skill that is only one of these words is not enough to search or to keep a job.
WEAK_TERMS = {
    "communication", "communications", "teamwork", "leadership", "interpersonal",
    "problem solving", "problem-solving", "hardworking", "dedicated", "motivated",
    "passionate", "team player", "fast learner", "business", "code", "skill",
    "skills", "management", "tools", "office", "support", "process", "quality",
    "development", "application", "applications", "system", "systems", "work",
    "team", "professional", "knowledge",
}

# Inside a longer skill, these words must not match a job on their own.
COMMON_TOKENS = WEAK_TERMS | {
    "data", "analysis", "software", "microsoft", "advanced", "technical", "general",
    "basic", "information", "technology", "impact", "creation", "computer", "digital",
    "using", "service", "services", "project", "projects", "working", "experience",
    "based", "strong", "power",
}


def _terms(values) -> list[str]:
    if not values:
        return []
    seen: list[str] = []
    for value in values:
        term = str(value).strip()
        if term and term.lower() not in {item.lower() for item in seen}:
            seen.append(term)
    return seen


def _posted_key(job: dict) -> str:
    return job.get("posted_at") or ""


def _keywords(term: str) -> list[str]:
    words = [word for word in re.split(r"[^a-z0-9+#]+", term.lower()) if len(word) > 2]
    specific = [word for word in words if word not in GENERIC_TITLE_WORDS]
    return specific or words


def distinctive_parts(term: str) -> list[str]:
    """Pieces of a skill or title that count as a close match.

    Generic role words are dropped. "Python Developer" can match on python.
    "Senior Engineer" matches on nothing, because both words are generic.
    """
    cleaned = term.strip()
    if not cleaned:
        return []
    tokens = [token for token in re.split(r"[^a-z0-9+#]+", cleaned.lower()) if token]
    specific = [token for token in tokens if len(token) > 2 and token not in GENERIC_TITLE_WORDS]
    short = [
        token for token in tokens
        if token not in specific and (re.search(r"[#+]", token) or token in SHORT_KEEP)
    ]
    if not specific and not short:
        return []
    parts = [cleaned]
    seen = {cleaned.lower()}
    for token in specific + short:
        if token not in seen:
            seen.add(token)
            parts.append(token)
    return parts


def _skill_terms(profile: dict) -> list[str]:
    return _terms([*(profile.get("must_have") or []), *(profile.get("nice_have") or [])])


def _usable(term: str) -> bool:
    cleaned = term.strip()
    return bool(cleaned) and cleaned.lower() not in WEAK_TERMS and bool(distinctive_parts(cleaned))


def _term_level(job_title: str, blob: str, part: str) -> str:
    if contains_term(job_title, part):
        return "heading"
    if contains_term(blob, part):
        return "body"
    return ""


def _overlap_level(job_title: str, blob: str, term: str) -> str:
    """Match a whole skill, or a rare word inside it.

    "Python" matches python. "Data Analysis" does not match every job that says
    data. "Power BI" can match on BI, not on the word power.
    """
    if not _usable(term):
        return ""
    parts = distinctive_parts(term)
    phrase, tokens = parts[0], parts[1:]
    phrase_level = _term_level(job_title, blob, phrase)
    if phrase_level:
        return phrase_level
    if not tokens:
        return ""
    rare = [token for token in tokens if token not in COMMON_TOKENS]
    if rare:
        best = ""
        for token in rare:
            level = _term_level(job_title, blob, token)
            if level == "heading":
                return "heading"
            if level:
                best = level
        return best
    best = ""
    for token in tokens:
        level = _term_level(job_title, blob, token)
        if not level:
            return ""
        if level == "heading":
            best = "heading"
        elif not best:
            best = level
    return best


def _sought(profile: dict) -> list[str]:
    return _terms(profile.get("seeking"))


_SENIORITY = {"senior", "junior", "lead", "intern", "associate"}


def _seeking_tokens(term: str) -> list[str]:
    """Words that identify a role, including analyst or developer.

    Seniority words are dropped. "Junior Python" is identified by python.
    "Data Analyst" keeps both words, so a mention of data alone is not enough.
    """
    tokens = [token for token in re.split(r"[^a-z0-9+#]+", term.lower()) if token]
    return [token for token in tokens if token not in _SENIORITY and (len(token) > 2 or token in SHORT_KEEP)]


def _seek_queries(term: str) -> list[str]:
    """Board queries for one role the person asked for.

    A multi-word role is searched as a phrase, so data analyst is not searched
    as data. A short role such as junior Python is also searched as python.
    """
    cleaned = term.strip()
    if not cleaned:
        return []
    required = _seeking_tokens(cleaned)
    if len(required) >= 2:
        return [cleaned]
    if len(required) == 1 and required[0] not in GENERIC_TITLE_WORDS and required[0] not in COMMON_TOKENS:
        if required[0] == cleaned.lower():
            return [cleaned]
        return [cleaned, required[0]]
    if len(cleaned.split()) >= 2:
        return [cleaned]
    return []


def query_keywords(profile: dict, limit: int = 6) -> list[str]:
    """The role they asked for first. Otherwise tool names, then rare skill words."""
    sought: list[str] = []
    for term in _sought(profile):
        sought.extend(_seek_queries(term))
    exact: list[str] = []
    rare: list[str] = []
    phrases: list[str] = []
    if not sought:
        for term in [*_skill_terms(profile), *_terms(profile.get("titles"))]:
            if not _usable(term):
                continue
            parts = distinctive_parts(term)
            phrases.append(parts[0])
            tokens = parts[1:]
            uncommon = [token for token in tokens if token not in COMMON_TOKENS]
            if len(tokens) == 1 and uncommon:
                exact.append(uncommon[0])
            else:
                rare.extend(uncommon)
    chosen: list[str] = []
    seen: set[str] = set()
    for term in [*sought, *exact, *rare, *phrases]:
        key = term.lower()
        if key in seen:
            continue
        seen.add(key)
        chosen.append(term)
        if len(chosen) >= limit:
            break
    return chosen


def search_block_reason(profile: dict) -> str | None:
    """Block only when there is nothing to search. An empty title is not an error."""
    if query_keywords(profile):
        return None
    resume = (profile.get("resume_text") or "").strip()
    skills = _skill_terms(profile)
    titles = _terms(profile.get("titles"))
    if resume or skills:
        return None
    if titles:
        return "Words like developer, engineer, and manager are not enough to search. Paste a resume that lists specific skills."
    return "Paste or upload a resume before searching."


def _seeking_level(job_title: str, _blob: str, term: str) -> str:
    """How closely a listing matches a role the person asked for.

    The role has to be in the job title. "Data Analyst" matches "Senior Data
    Analyst" and "Analyst, Data Science". It does not match a risk analyst, or
    a coordinator whose description merely says data. "Junior Python" matches a
    title that says python. "Senior Engineer" has to appear as that phrase.
    """
    cleaned = term.strip()
    if not cleaned:
        return ""
    required = _seeking_tokens(cleaned)
    if not required or (len(required) == 1 and required[0] in GENERIC_TITLE_WORDS):
        if len(cleaned.split()) >= 2 and contains_term(job_title, cleaned):
            return "heading"
        return ""
    if len(required) == 1:
        return "heading" if contains_term(job_title, required[0]) else ""
    if contains_term(job_title, cleaned) or all(contains_term(job_title, token) for token in required):
        return "heading"
    return ""


def score_job(job: dict, profile: dict) -> tuple[int, list[str]] | None:
    titles = _terms(profile.get("titles"))
    excluded = _terms(profile.get("exclude"))
    job_title = job.get("title") or ""
    blob = " ".join(
        part for part in (job_title, job.get("company"), job.get("description"), job.get("location")) if part
    )
    if any(contains_term(blob, term) or contains_term(job.get("company") or "", term) for term in excluded):
        return None
    if profile.get("work_mode") == "remote" and job.get("source") == "Bdjobs" and not job.get("is_remote"):
        return None

    must_hits = [
        term for term in _terms(profile.get("must_have"))
        if _usable(term) and _overlap_level(job_title, blob, term)
    ]
    nice_hits = [
        term for term in _terms(profile.get("nice_have"))
        if _usable(term) and term not in must_hits and _overlap_level(job_title, blob, term)
    ]
    skill_hits = must_hits + nice_hits
    title_term = ""
    title_level = ""
    for term in titles:
        level = _overlap_level(job_title, blob, term)
        if not level:
            continue
        if level == "heading" or not title_level:
            title_term = term
            title_level = level
        if level == "heading":
            break
    seeking_terms = [term for term in _sought(profile) if _usable(term) or len(term.split()) >= 2]
    seek_term = ""
    seek_level = ""
    title_points = 200 if title_level == "heading" else 100 if title_level == "body" else 0
    skill_points = len(must_hits) * 1000 + len(nice_hits) * 150
    if seeking_terms:
        for term in seeking_terms:
            level = _seeking_level(job_title, blob, term)
            if not level:
                continue
            if level == "heading" or not seek_level:
                seek_term = term
                seek_level = level
            if level == "heading":
                break
        if not seek_level:
            return None
        # The role they asked for outranks resume skills. Skills still order those jobs.
        seek_points = 6000 if seek_level == "heading" else 2000 if seek_level == "body" else 0
        score = seek_points + skill_points + title_points
        overlap = True
    else:
        # Resume skills outrank optional skills, which outrank a title hit.
        # A listing with no shared skill or title stays on the board, below those matches.
        overlap = bool(skill_hits or title_level)
        score = skill_points + title_points
    reasons: list[str] = []
    if seek_term:
        prefix = "You asked for" if seek_level == "heading" else "Fits your request"
        reasons.append(f"{prefix} {seek_term}")
    if skill_hits:
        reasons.append("Skills: " + ", ".join(skill_hits[:4]))
    if title_term:
        prefix = "Title matches" if title_level == "heading" else "Mentions"
        reasons.append(f"{prefix} {title_term}")
    if not overlap:
        reasons.append("Other open listing")

    location = (job.get("location") or "").lower()
    city = (profile.get("city") or "").strip()
    worldwide = any(word in location for word in ("anywhere", "worldwide", "world", "global", "remote"))
    apac = "apac" in location or "asia" in location
    in_bangladesh = job.get("source") == "Bdjobs" or "bangladesh" in location or "dhaka" in location
    if city and city.lower() in location:
        score += 18
        reasons.append(f"In {city}")
    elif in_bangladesh and profile.get("work_mode") != "remote":
        score += 8
        reasons.append("Bangladesh")
    if job.get("is_remote") and (worldwide or apac or job.get("source") != "Bdjobs"):
        score += 12
        reasons.append("Remote")
    restriction = (job.get("location_note") or "").strip()
    if restriction and not worldwide and "bangladesh" not in restriction.lower():
        score -= 6
        reasons.append(f"Listing is limited to {restriction}")
    if not overlap:
        # The smallest skill or title match is 100, before a listing limit of 6.
        score = min(score, 50)
    return max(score, 1), reasons[:5]


def rank_jobs(jobs: list[dict], profile: dict) -> list[dict]:
    ranked: list[dict] = []
    for job in jobs:
        scored = score_job(job, profile)
        if scored is None:
            continue
        score, reasons = scored
        ranked.append({**job, "score": score, "reasons": reasons})
    ranked.sort(key=lambda job: (job["score"], _posted_key(job)), reverse=True)
    return ranked


def select_visible(ranked: list[dict], limit: int = 60) -> list[dict]:
    """At most `limit` listings, with close matches first.

    Jobs that share a skill or past title stay above the others. When those
    matches would fill the board, the last third is left for other listings
    from the same fetch.
    """
    if limit <= 0:
        return []
    matches: list[dict] = []
    others: list[dict] = []
    for job in ranked:
        if "Other open listing" in (job.get("reasons") or []):
            others.append(job)
        else:
            matches.append(job)
    if len(matches) >= limit and others:
        other_slots = min(len(others), max(1, limit // 3))
        match_slots = limit - other_slots
    else:
        match_slots = min(len(matches), limit)
        other_slots = min(len(others), limit - match_slots)
    return matches[:match_slots] + others[:other_slots]
