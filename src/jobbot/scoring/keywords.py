"""Keyword sets for classification and relevance scoring."""

from __future__ import annotations

import re

INTERNSHIP_INDICATORS: frozenset[str] = frozenset(
    {"intern", "internship", "co-op", "coop", "co op", "student", "placement"}
)

SOFTWARE_INDICATORS: frozenset[str] = frozenset(
    {
        "software",
        "developer",
        "engineering",
        "engineer",
        "backend",
        "back end",
        "frontend",
        "front end",
        "full stack",
        "full-stack",
        "fullstack",
        "mobile",
        "ios",
        "android",
        "machine learning",
        "ml",
        "ai",
        "platform",
        "infrastructure",
        "security",
        "firmware",
        "embedded",
        "systems",
        "devops",
        "sre",
        "site reliability",
        "data engineer",
        "programming",
        "web developer",
        "qa automation",
    }
)

# Strong negatives: seniority or non-software disciplines. Weighted heavily.
NEGATIVE_KEYWORDS: frozenset[str] = frozenset(
    {
        "senior software engineer",
        "staff software engineer",
        "principal engineer",
        "principal software engineer",
        "engineering manager",
        "internship program manager",
        "sales intern",
        "marketing intern",
        "recruiting intern",
        "hr intern",
        "finance intern",
        "mechanical engineering intern",
        "mechanical engineer intern",
        "civil engineering intern",
        "electrical engineering intern",
        "chemical engineering intern",
        "unpaid",
        "bootcamp",
        "course",
        "aggregator",
    }
)

# Non-software engineering disciplines that should NOT count as software unless a
# software indicator is also present.
NON_SOFTWARE_DISCIPLINES: frozenset[str] = frozenset(
    {"mechanical", "civil", "electrical", "chemical", "biomedical", "industrial"}
)

# Category detection keywords -> category tag.
CATEGORY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "backend": ("backend", "back end", "back-end", "server"),
    "frontend": ("frontend", "front end", "front-end", "ui engineer", "web developer"),
    "fullstack": ("full stack", "full-stack", "fullstack"),
    "mobile": ("mobile", "ios", "android"),
    "machine_learning": ("machine learning", "ml engineer", " ai ", "deep learning"),
    "infrastructure": (
        "infrastructure",
        "platform",
        "devops",
        "sre",
        "site reliability",
        "cloud",
    ),
    "security": ("security", "appsec", "infosec"),
    "embedded": ("embedded", "firmware", "systems software"),
}


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").lower())


def contains_any(text: str, terms) -> list[str]:
    t = _norm(text)
    found = []
    for term in terms:
        # word-boundary-ish match to avoid 'ml' matching 'html'
        pattern = r"(?<![a-z0-9])" + re.escape(term) + r"(?![a-z0-9])"
        if re.search(pattern, t):
            found.append(term)
    return found
