import io
import re

SKILL_HINTS = (
    "python", "django", "flask", "fastapi", "javascript", "typescript", "react", "node.js",
    "java", "c#", "c++", "sql", "excel", "powerpoint", "wordpress", "php", "laravel",
    "html", "css", "git", "docker", "aws", "azure", "linux", "figma", "photoshop",
    "sales", "marketing", "seo", "accounting", "communication", "customer support",
    "data analysis", "power bi", "tableau", "machine learning",
)

SECTION_NAMES = {
    "skills", "technical skills", "core skills", "key skills", "experience",
    "work experience", "professional experience", "employment history", "work history",
    "education", "summary", "professional summary", "profile", "projects",
    "certifications", "contact", "languages",
}

LABEL_SKIP = {
    "tools & software", "tools and software", "programming & databases",
    "programming and databases", "domain knowledge", "technical skills",
    "core skills", "key skills", "skills",
}

STOP_SKILLS = {
    "management", "understanding", "knowledge", "tools", "programming", "databases",
    "domain", "business", "code", "study", "preparation", "and", "the", "with",
    "data", "analysis", "software", "quality", "process",
}

NON_NAME_WORDS = {
    "summary", "skills", "skill", "experience", "education", "projects", "project",
    "resume", "curriculum", "profile", "objective", "professional", "technical",
    "contact", "linkedin", "github", "phone", "email", "address", "intern",
}

CITY_NAMES = (
    ("chattogram", "Chattogram"),
    ("chittagong", "Chittagong"),
    ("narayanganj", "Narayanganj"),
    ("mymensingh", "Mymensingh"),
    ("rajshahi", "Rajshahi"),
    ("barishal", "Barishal"),
    ("barisal", "Barisal"),
    ("rangpur", "Rangpur"),
    ("gazipur", "Gazipur"),
    ("cumilla", "Cumilla"),
    ("comilla", "Comilla"),
    ("sylhet", "Sylhet"),
    ("khulna", "Khulna"),
    ("dhaka", "Dhaka"),
)


def _match_header(line: str) -> tuple[str, str] | None:
    """Return a section name and any text joined onto that header."""
    lowered = line.lower().strip()
    for name in sorted(SECTION_NAMES, key=len, reverse=True):
        if lowered == name or lowered == f"{name}:":
            return name, ""
        if lowered.startswith(f"{name}:"):
            return name, line.split(":", 1)[1].strip()
        if lowered.startswith(f"{name} "):
            return name, line[len(name):].strip(" :")
    return None


def _section(lines: list[str], names: set[str]) -> list[str]:
    collected: list[str] = []
    started = False
    for line in lines:
        header = _match_header(line)
        if header and header[0] in names and not started:
            started = True
            if header[1]:
                collected.append(header[1])
            continue
        if not started:
            continue
        if header and header[0] in SECTION_NAMES:
            break
        if line:
            collected.append(line)
    return collected


def _split_list(text: str) -> list[str]:
    parts: list[str] = []
    buf: list[str] = []
    depth = 0
    for char in text:
        if char == "(":
            depth += 1
        elif char == ")" and depth:
            depth -= 1
        if char in ",;•|" and depth == 0:
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(char)
    parts.append("".join(buf))
    return [part.strip(" -\t") for part in parts if part.strip(" -\t")]


def _is_prose(text: str) -> bool:
    lowered = text.lower()
    if re.search(r"\b(through|understanding of|responsible for|experience in|worked on|ability to)\b", lowered):
        return True
    words = text.split()
    if text.count(",") >= 1 and len(words) <= 40:
        return False
    if len(words) > 14:
        return True
    return text.rstrip().endswith(".") and len(words) > 6


def _remember_skill(skills: list[str], skill: str) -> None:
    skill = re.sub(r"\s+for\b.*$", "", skill.strip(" -\t.•"), flags=re.I).strip()
    if not (1 < len(skill) <= 40) or skill.endswith(":"):
        return
    if skill.lower() in STOP_SKILLS or _is_prose(skill):
        return
    if len(skill.split()) > 6:
        return
    if skill not in skills:
        skills.append(skill)


def _add_skill_piece(skills: list[str], piece: str) -> None:
    piece = piece.strip(" -\t•")
    match = re.match(r"^([^()]{1,40})\(([^()]*)\)\s*$", piece)
    if not match:
        _remember_skill(skills, piece)
        return
    _remember_skill(skills, match.group(1))
    inner = match.group(2).strip()
    if inner and not _is_prose(inner):
        for bit in _split_list(inner):
            _remember_skill(skills, bit)


def _unwrap_lines(lines: list[str]) -> list[str]:
    merged: list[str] = []
    for line in lines:
        new_category = bool(re.match(r"^[A-Za-z][^:]{0,40}:\s+\S", line))
        if merged and not merged[-1].rstrip().endswith((",", ";", ".", ":")) and not new_category:
            merged[-1] = f"{merged[-1].rstrip()} {line.lstrip()}"
        else:
            merged.append(line)
    return merged


def _split_skills(lines: list[str]) -> list[str]:
    skills: list[str] = []
    for line in _unwrap_lines(lines):
        body = line
        label = ""
        if ":" in line:
            label, body = line.split(":", 1)
            label = label.strip()
        if _is_prose(body):
            continue
        if label and label.lower() not in LABEL_SKIP:
            _remember_skill(skills, label)
        for piece in _split_list(body):
            _add_skill_piece(skills, piece)
        if len(skills) >= 24:
            break
    return skills[:24]


def _header_city(lines: list[str]) -> str:
    for line in lines:
        lowered = line.lower()
        for needle, label in CITY_NAMES:
            if re.search(rf"(?<![a-z]){needle}(?![a-z])", lowered):
                return label
    return ""


def _work_mode(text: str) -> str:
    if re.search(r"\b(remote[\s-]*only|only remote|fully remote|100% remote|exclusively remote)\b", text.lower()):
        return "remote"
    return ""


def _break_headers(text: str) -> str:
    """Put a line break before a section header that PDF extraction glued on."""
    names = sorted(SECTION_NAMES, key=len, reverse=True)
    pattern = "|".join(re.escape(name) for name in names)
    return re.sub(rf"(?i)(?<=\S)\s+(?:{pattern})\b", lambda match: "\n" + match.group(0).strip(), text)


def _looks_like_name(line: str) -> bool:
    if not line or any(char.isdigit() for char in line) or re.search(r"[:@|/()]", line):
        return False
    if line.endswith("."):
        return False
    words = line.split()
    if not 2 <= len(words) <= 4 or not 5 <= len(line) <= 60:
        return False
    if not all(re.fullmatch(r"[A-Za-z][A-Za-z.'-]*", word) for word in words):
        return False
    if not any(len(word) > 2 for word in words):
        return False
    if any(word.lower() in NON_NAME_WORDS for word in words):
        return False
    if _match_header(line):
        return False
    return any(word[:1].isupper() for word in words)


def _name_candidate(line: str) -> str:
    without = re.sub(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", " ", line)
    without = re.sub(r"(?:\+\d{1,3}[\s-]?)?(?:\d[\s()-]?){8,}\d", " ", without)
    without = re.sub(r"[|•·]", " ", without)
    without = re.sub(r"\s+", " ", without).strip(" -")
    return without if _looks_like_name(without) else ""


def _find_name(lines: list[str]) -> str:
    contact_at = [
        index for index, line in enumerate(lines)
        if "@" in line or re.search(r"(?:\+\d{1,3}[\s-]?)?(?:\d[\s()-]?){8,}\d", line)
    ]
    for index in contact_at:
        for neighbor in (index - 1, index + 1, index - 2, index + 2):
            if 0 <= neighbor < len(lines):
                found = _name_candidate(lines[neighbor])
                if found:
                    return found
        found = _name_candidate(lines[index])
        if found:
            return found
    for line in lines[:8]:
        found = _name_candidate(line)
        if found:
            return found
    return ""


_MONTHS = {
    "january", "february", "march", "april", "may", "june", "july", "august",
    "september", "october", "november", "december",
    "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept", "oct", "nov", "dec",
}


def _is_date_line(line: str) -> bool:
    return bool(re.search(r"\b(19|20)\d{2}\b", line))


def _could_be_title(line: str) -> bool:
    if not line or line.endswith(".") or line.startswith(("-", "•", "*")):
        return False
    if _is_date_line(line) or _match_header(line):
        return False
    if line.lower().strip(".,") in _MONTHS:
        return False
    if any(word in line.lower() for word in ("ltd", "limited", "inc.", "pvt")):
        return False
    return 3 <= len(line) <= 90 and len(line.split()) <= 12


def _titles_from(lines: list[str]) -> list[str]:
    titles: list[str] = []
    for index, line in enumerate(lines):
        if line.startswith(("-", "•", "*")) or line.endswith("."):
            continue
        original = line
        if _is_date_line(line):
            line = re.split(r"\b(?:19|20)\d{2}\b", line, maxsplit=1)[0].strip(" -–—|,")
            if not _could_be_title(line):
                continue
        upcoming = lines[index + 1] if index + 1 < len(lines) else ""
        after = lines[index + 2] if index + 2 < len(lines) else ""
        previous = lines[index - 1] if index else ""
        is_role = (
            _is_date_line(original)
            or "|" in line
            or _is_date_line(upcoming)
            or (_could_be_title(upcoming) and _is_date_line(after))
        )
        if not is_role:
            continue
        if (
            _is_date_line(upcoming)
            and _could_be_title(previous)
            and "|" not in line
            and "|" not in previous
        ):
            continue
        title = line.split("|", 1)[0].strip(" -–—\t")
        if not _could_be_title(title) or title in titles:
            continue
        titles.append(title)
        if len(titles) == 4:
            break
    return titles


def parse_resume(text: str) -> dict:
    raw = _break_headers(text.replace("\r\n", "\n").replace("\r", "\n")).strip()
    lines = [line.strip() for line in raw.split("\n")]
    lines = [line for line in lines if line]
    email_match = re.search(r"[\w.+-]+@[\w.-]+\.[a-zA-Z]{2,}", raw)
    phone_match = re.search(r"(?:\+\d{1,3}[\s-]?)?(?:\d[\s()-]?){8,}\d", raw)
    contact_parts = [part for part in (email_match.group(0) if email_match else "", phone_match.group(0).strip() if phone_match else "") if part]
    skill_lines = _section(lines, {"skills", "technical skills", "core skills", "key skills"})
    skills = _split_skills(skill_lines)
    lowered = raw.lower()
    known = {item.lower() for item in skills}
    for hint in SKILL_HINTS:
        if hint.lower() in known:
            continue
        if re.search(rf"(?<![a-z0-9+#.]){re.escape(hint)}(?![a-z0-9+#])", lowered):
            skills.append(hint)
            known.add(hint.lower())
    skills = skills[:24]
    titles = _titles_from(_section(lines, {"experience", "work experience", "professional experience", "employment history", "work history"}))
    return {
        "name": _find_name(lines),
        "contact": " · ".join(contact_parts),
        "titles": titles,
        "skills": skills,
        "city": _header_city(lines),
        "work_mode": _work_mode(raw),
    }


def _lines_from_page(page) -> list[str]:
    """Rebuild visual lines. Plain extraction glues separate lines into one string."""
    chunks: list[tuple[float, float, str, float]] = []

    def visitor(text, _cm, tm, _font, size):
        piece = str(text or "").replace("\x00", "")
        if not piece.strip():
            return
        chunks.append((float(tm[5]), float(tm[4]), piece, float(size or 12)))

    plain = page.extract_text(visitor_text=visitor) or ""
    if not chunks:
        return [line.strip() for line in plain.splitlines() if line.strip()]
    chunks.sort(key=lambda item: (-item[0], item[1]))
    buckets: list[list[tuple[float, str, float]]] = []
    current_y: float | None = None
    bucket: list[tuple[float, str, float]] = []
    for y, x, piece, size in chunks:
        if current_y is None or abs(y - current_y) > 4:
            if bucket:
                buckets.append(bucket)
            bucket = [(x, piece, size)]
            current_y = y
        else:
            bucket.append((x, piece, size))
    if bucket:
        buckets.append(bucket)
    lines: list[str] = []
    for group in buckets:
        group.sort(key=lambda item: item[0])
        pieces: list[str] = []
        cursor: float | None = None
        for x, piece, size in group:
            gap = None if cursor is None else x - cursor
            if (
                gap is not None
                and gap > max(size, 8) * 0.18
                and pieces
                and not pieces[-1].endswith((" ", "-", "–", "/"))
                and not piece.startswith((" ", ",", ".", ";", ":", ")", "/"))
            ):
                pieces.append(" ")
            pieces.append(piece)
            cursor = x + max(size, 8) * 0.45 * max(len(piece), 1)
        line = re.sub(r"[ \t]{2,}", " ", "".join(pieces)).strip()
        if line:
            lines.append(line)
    return lines


def extract_pdf_text(data: bytes) -> str:
    """Pull plain text from a PDF. Raises ValueError when the file cannot be read."""
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise ValueError("PDF reading is not installed. Paste the resume text instead.") from exc
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            try:
                unlocked = reader.decrypt("")
            except Exception as exc:
                raise ValueError("Could not read that PDF. Paste the resume text instead.") from exc
            if not unlocked:
                raise ValueError("Could not read that PDF. Paste the resume text instead.")
        parts = ["\n".join(_lines_from_page(page)) for page in reader.pages]
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError("Could not read that PDF. Paste the resume text instead.") from exc
    text = "\n".join(part for part in parts if part).replace("\x00", "").strip()
    if not re.search(r"[A-Za-z0-9]", text):
        return ""
    return text
