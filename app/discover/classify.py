"""Role families, skills, and reason chips taken from a posting's own text."""

from __future__ import annotations

import re

FAMILIES = (
    "data_analyst",
    "ml_mlops",
    "business_analyst",
    "operations",
    "product",
    "it_bank_tech",
)

# When two families score the same, the more specific one wins.
TIE_BREAK = (
    "ml_mlops",
    "business_analyst",
    "data_analyst",
    "product",
    "operations",
    "it_bank_tech",
)

FAMILY_LABELS = {
    "data_analyst": "Data analyst",
    "ml_mlops": "ML / MLOps",
    "business_analyst": "Business analyst",
    "operations": "Operations",
    "product": "Product",
    "it_bank_tech": "IT / bank tech",
}

FAMILY_PHRASES = {
    "ml_mlops": (
        "machine learning",
        "mlops",
        "ml ops",
        "ml engineer",
        "deep learning",
        "data scientist",
        "model deployment",
    ),
    "business_analyst": (
        "business analyst",
        "business analysis",
        "requirements analyst",
        "functional analyst",
    ),
    "data_analyst": (
        "data analyst",
        "data analytics",
        "business intelligence",
        "bi analyst",
        "power bi",
        "tableau",
    ),
    "product": (
        "product manager",
        "product owner",
        "product management",
        "product analyst",
    ),
    "operations": (
        "operations manager",
        "business operations",
        "supply chain",
        "logistics",
        "process improvement",
        "operations",
    ),
    "it_bank_tech": (
        "core banking",
        "it officer",
        "it support",
        "information technology",
        "system administrator",
        "banking technology",
        "network administrator",
        "fintech",
        "bank",
    ),
}

SKILL_PHRASES = (
    "machine learning",
    "power bi",
    "supply chain",
    "core banking",
    "kubernetes",
    "tensorflow",
    "pytorch",
    "tableau",
    "airflow",
    "docker",
    "python",
    "pandas",
    "spark",
    "excel",
    "mlops",
    "linux",
    "java",
    "sql",
    "aws",
    "networking",
    "logistics",
)

ADVANCED_SKILLS = {
    "machine learning",
    "kubernetes",
    "tensorflow",
    "pytorch",
    "airflow",
    "docker",
    "spark",
    "mlops",
}

TECH_SKILLS = ADVANCED_SKILLS | {
    "python",
    "sql",
    "java",
    "linux",
    "pandas",
    "aws",
    "tableau",
    "power bi",
    "networking",
}

INDUSTRY_PHRASES = (
    ("banking", ("core banking", "banking", "fintech", "bank")),
    ("retail", ("e-commerce", "ecommerce", "retail")),
    ("healthcare", ("healthcare", "health care", "hospital", "pharma")),
    ("telecom", ("telecommunications", "telecom")),
    ("software", ("software", "saas")),
    ("government", ("public sector", "government")),
)

SENIORITY_RULES = (
    ("lead", ("principal", "staff engineer", "head of", "director", "manager", "lead")),
    ("senior", ("senior", "sr")),
    ("junior", ("junior", "entry level", "entry-level", "graduate", "internship", "intern", "associate")),
    ("mid", ("mid-level", "mid level", "intermediate")),
)

CHIP_LABELS = {
    "salary": "Salary",
    "role_type": "Role type",
    "company": "Company",
    "location": "Location",
    "seniority": "Seniority",
    "too_technical": "Too technical",
    "not_technical_enough": "Not technical enough",
    "industry": "Industry",
}

SUMMARY_EVERY = 25
BATCH_SIZE = 20


def _has(text: str, phrase: str) -> bool:
    pattern = rf"(?<![a-z0-9]){re.escape(phrase.lower())}(?![a-z0-9])"
    return re.search(pattern, text.lower()) is not None


def classify(title: str, description: str) -> str:
    text = f"{title}\n{description}".lower()
    scores: dict[str, int] = {}
    for family, phrases in FAMILY_PHRASES.items():
        score = 0
        for phrase in phrases:
            if _has(text, phrase):
                score += max(1, len(phrase.split()))
        if score:
            scores[family] = score
    if not scores:
        return "other"
    best = max(scores.values())
    for family in TIE_BREAK:
        if scores.get(family) == best:
            return family
    return "other"


def extract_skills(title: str, description: str) -> list[str]:
    text = f"{title}\n{description}".lower()
    found: list[str] = []
    for skill in SKILL_PHRASES:
        if _has(text, skill) and skill not in found:
            found.append(skill)
    return found[:6]


def technical_level(skills: list[str]) -> str:
    chosen = set(skills)
    if chosen & ADVANCED_SKILLS or len(chosen & TECH_SKILLS) >= 3:
        return "high"
    if chosen & TECH_SKILLS:
        return "medium"
    return "low"


def detect_industry(title: str, description: str) -> str:
    text = f"{title}\n{description}".lower()
    best = ""
    best_score = 0
    for industry, phrases in INDUSTRY_PHRASES:
        score = 0
        for phrase in phrases:
            if _has(text, phrase):
                score = max(score, len(phrase.split()))
        if score > best_score:
            best = industry
            best_score = score
    return best


def detect_seniority(title: str, description: str) -> str:
    text = f"{title}\n{description}".lower()
    for level, phrases in SENIORITY_RULES:
        if any(_has(text, phrase) for phrase in phrases):
            return level
    return "unknown"


def three_line_summary(description: str) -> str:
    text = re.sub(r"\s+", " ", description or "").strip()
    if not text:
        return ""
    sentences = [part.strip() for part in re.split(r"(?<=[.!?])\s+", text) if part.strip()]
    if len(sentences) >= 3 or not (len(sentences) == 1 and len(sentences[0]) > 220):
        return "\n".join(sentences[:3])
    words = sentences[0].split()
    lines: list[str] = []
    current: list[str] = []
    for word in words:
        current.append(word)
        if len(" ".join(current)) >= 80 and len(lines) < 2:
            lines.append(" ".join(current))
            current = []
    if current:
        lines.append(" ".join(current))
    return "\n".join(lines[:3])


def chips_for(features: dict) -> list[str]:
    chips: list[str] = []
    if features.get("salary"):
        chips.append("salary")
    if features.get("role_family") not in {"", "other"} or features.get("title"):
        chips.append("role_type")
    if features.get("company"):
        chips.append("company")
    if features.get("location") or features.get("is_remote"):
        chips.append("location")
    if features.get("seniority") not in {"", "unknown"}:
        chips.append("seniority")
    if features.get("technical_level") == "high":
        chips.append("too_technical")
    if features.get("technical_level") == "low":
        chips.append("not_technical_enough")
    if features.get("industry"):
        chips.append("industry")
    return chips


def city_key(location: str) -> str:
    first = (location or "").split(",")[0].strip().lower()
    if first in {"", "remote", "worldwide", "anywhere", "hybrid"}:
        return ""
    return first


def extract(job: dict) -> dict | None:
    posting_id = str(job.get("id") or job.get("posting_id") or "").strip()
    title = str(job.get("title") or "").strip()
    if not posting_id or not title:
        return None
    description = str(job.get("description") or "")
    company = str(job.get("company") or "").strip()
    location = str(job.get("location") or "").strip()
    salary = str(job.get("salary") or "").strip()
    blob = f"{title}\n{description}\n{location}"
    remote = bool(job.get("is_remote")) or any(_has(blob, word) for word in ("remote", "worldwide", "anywhere"))
    skills = extract_skills(title, description)
    features = {
        "posting_id": posting_id,
        "title": title,
        "company": company,
        "location": location,
        "salary": salary,
        "is_remote": remote,
        "role_family": classify(title, description),
        "industry": detect_industry(title, description),
        "seniority": detect_seniority(title, description),
        "skills": skills,
        "summary": three_line_summary(description),
        "technical_level": technical_level(skills),
        "city": city_key(location),
        "source": str(job.get("source") or ""),
    }
    features["chips"] = chips_for(features)
    return features


def present_card(features: dict) -> dict:
    return {
        "posting_id": features["posting_id"],
        "title": features["title"],
        "company": features["company"],
        "location": features["location"],
        "salary": features["salary"],
        "role_family": features["role_family"],
        "role_family_label": FAMILY_LABELS.get(features["role_family"], "Other"),
        "summary": features["summary"],
        "skills": list(features["skills"][:5]),
        "chips": [{"id": chip, "label": CHIP_LABELS[chip]} for chip in features["chips"]],
        "is_remote": features["is_remote"],
    }
