from app.match import _overlap_level
from app.textutil import contains_term

_HEADINGS = {
    "summary": {"summary", "professional summary", "profile", "objective", "about", "about me"},
    "skills": {"skills", "technical skills", "core skills", "key skills"},
    "experience": {
        "experience",
        "work experience",
        "professional experience",
        "employment",
        "employment history",
        "work history",
    },
    "education": {"education", "academic background", "academics"},
    "projects": {"projects", "personal projects", "selected projects"},
}


def _ordered_skills(profile: dict, job_text: str) -> list[str]:
    pool = [*profile.get("must_have", []), *profile.get("nice_have", [])]
    seen: list[str] = []
    for skill in pool:
        cleaned = str(skill).strip()
        if cleaned and cleaned.lower() not in {item.lower() for item in seen}:
            seen.append(cleaned)
    matched = [skill for skill in seen if _skill_in_job(job_text, skill)]
    rest = [skill for skill in seen if skill not in matched]
    return matched + rest


def _skill_in_job(job_text: str, skill: str) -> bool:
    if contains_term(job_text, skill):
        return True
    return bool(_overlap_level("", job_text, skill))


def resume_blocks(text: str) -> dict[str, str]:
    """Split a resume into the sections it already has. Wording is left as written."""
    raw = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    aliases = {alias: name for name, names in _HEADINGS.items() for alias in names}
    current = "preamble"
    buckets: dict[str, list[str]] = {
        "preamble": [],
        "summary": [],
        "skills": [],
        "experience": [],
        "education": [],
        "projects": [],
        "other": [],
    }
    for line in raw.split("\n"):
        key = line.strip().lower().strip(":")
        if key in aliases:
            current = aliases[key]
            continue
        buckets[current].append(line.rstrip())
    return {name: "\n".join(lines).strip() for name, lines in buckets.items()}


def assemble_cv(profile: dict, job: dict, summary: str) -> str:
    """Resume for one job: the role, then facts already stored, aimed at that role.

    The summary is the only new prose. Skills that appear in the posting move up.
    Experience, projects, and education are copied from the stored resume.
    """
    name = (profile.get("name") or "Your name").strip() or "Your name"
    contact = (profile.get("contact") or "").strip()
    title = (job.get("title") or "Role").strip() or "Role"
    job_text = f"{title}\n{job.get('description') or ''}"
    skills = _ordered_skills(profile, job_text)
    skill_line = ", ".join(skills) if skills else "See experience below."
    blocks = resume_blocks(profile.get("resume_text") or "")
    header = name if not contact else f"{name}\n{contact}"
    parts = [header, "", title, "", "Summary", (summary or "").strip(), "", "Skills", skill_line]
    experience = blocks.get("experience") or ""
    if not experience:
        experience = (profile.get("resume_text") or "").strip()
    if experience:
        parts.extend(["", "Experience", experience])
    if blocks.get("projects"):
        parts.extend(["", "Projects", blocks["projects"]])
    if blocks.get("education"):
        parts.extend(["", "Education", blocks["education"]])
    return "\n".join(parts).strip() + "\n"


def extract_summary(cv: str) -> str:
    if "Summary\n" not in (cv or ""):
        return ""
    body = cv.split("Summary\n", 1)[1]
    return body.split("\n\nSkills\n", 1)[0].strip()


def template_summary(profile: dict, job: dict) -> str:
    title = (job.get("title") or "this role").strip() or "this role"
    job_text = f"{title}\n{job.get('description') or ''}"
    matched = [skill for skill in _ordered_skills(profile, job_text) if _skill_in_job(job_text, skill)][:4]
    skills = ", ".join(matched) if matched else "the skills listed below"
    titles = [str(item).strip() for item in (profile.get("titles") or []) if str(item).strip()]
    history = ""
    if titles:
        history = " Experience includes " + " and ".join(titles[:2]) + "."
    return f"Applying for the {title} role. The skills aimed at this posting are {skills}.{history}"


def template_cover(profile: dict, job: dict) -> str:
    title = (job.get("title") or "open").strip() or "open"
    job_text = f"{title}\n{job.get('description') or ''}"
    matched = [skill for skill in _ordered_skills(profile, job_text) if _skill_in_job(job_text, skill)][:3]
    skills = ", ".join(matched) if matched else "the work described in my resume"
    name = (profile.get("name") or "").strip()
    city = (profile.get("city") or "Bangladesh").strip() or "Bangladesh"
    where = "remotely" if profile.get("work_mode") == "remote" else f"in {city} or remotely"
    company = (job.get("company") or "").strip() or "hiring"
    closing = f"\n\n{name}" if name else ""
    return (
        f"Hello {company} team,\n\n"
        f"I am applying for the {title} role. "
        f"I can contribute with {skills}. I am based in {city} and can work {where}."
        f"{closing}"
    )


def template_documents(profile: dict, job: dict) -> tuple[str, str]:
    summary = template_summary(profile, job)
    return assemble_cv(profile, job, summary), template_cover(profile, job)
