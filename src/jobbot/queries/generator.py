"""Query generation.

We deliberately avoid a blind Cartesian product. Instead we build a bounded set
of *templates* per (platform, title-group), optionally decorated with an
academic-term clause or a location clause. Each scheduled run then selects a
prioritized, rotated batch so runs cover different combinations over time while
still favouring historically high-yield queries.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from pydantic import BaseModel, Field

from jobbot.queries.terms import (
    DEFAULT_ACADEMIC_TERMS,
    DEFAULT_LOCATIONS,
    JOB_TITLE_GROUPS,
    PLATFORMS,
    TITLE_GROUP_TO_CATEGORY,
)

# Relative weight per title group — generic SWE roles are the highest value.
_GROUP_WEIGHT: dict[str, float] = {
    "generic_swe": 1.5,
    "backend": 1.2,
    "frontend": 1.1,
    "fullstack": 1.1,
    "mobile": 1.0,
    "platform_infra": 1.0,
    "data_ml": 1.1,
    "security": 0.9,
    "embedded": 0.8,
    "other": 0.7,
}


class QueryGenConfig(BaseModel):
    """Runtime configuration for query generation (merged from guild settings)."""

    enabled_platforms: list[str] = Field(default_factory=lambda: list(PLATFORMS.keys()))
    academic_terms: list[str] = Field(default_factory=lambda: list(DEFAULT_ACADEMIC_TERMS))
    locations: list[str] = Field(default_factory=lambda: list(DEFAULT_LOCATIONS))
    include_location_variants: bool = True
    include_term_variants: bool = True
    max_titles_per_clause: int = 4
    max_locations_per_clause: int = 5


@dataclass(frozen=True)
class GeneratedQuery:
    text: str
    platform_slug: str
    group: str
    category: str
    priority: float
    variant: str  # "title" | "title_term" | "title_location"
    query_hash: str = field(default="", compare=False)

    def with_hash(self) -> GeneratedQuery:
        h = hashlib.sha256(self.text.encode()).hexdigest()[:32]
        return GeneratedQuery(
            text=self.text,
            platform_slug=self.platform_slug,
            group=self.group,
            category=self.category,
            priority=self.priority,
            variant=self.variant,
            query_hash=h,
        )


def _or_clause(terms: list[str]) -> str:
    parts = [f'"{t}"' if " " in t or "-" in t else t for t in terms]
    return "(" + " OR ".join(parts) + ")"


def _titles_for_group(titles: list[str], cap: int) -> list[str]:
    return titles[:cap]


def build_queries(config: QueryGenConfig | None = None) -> list[GeneratedQuery]:
    """Build the full candidate query set (deterministic, no I/O)."""
    config = config or QueryGenConfig()
    out: list[GeneratedQuery] = []

    for slug in config.enabled_platforms:
        platform = PLATFORMS.get(slug)
        if not platform:
            continue
        domain = platform[1]
        for group, titles in JOB_TITLE_GROUPS.items():
            category = TITLE_GROUP_TO_CATEGORY[group]
            weight = _GROUP_WEIGHT.get(group, 1.0)
            title_clause = _or_clause(_titles_for_group(titles, config.max_titles_per_clause))

            # 1. Title-only — broadest, highest coverage.
            out.append(
                GeneratedQuery(
                    text=f"site:{domain} {title_clause}",
                    platform_slug=slug,
                    group=group,
                    category=category,
                    priority=weight * 1.0,
                    variant="title",
                ).with_hash()
            )

            # 2. Title + academic term clause — targets a specific cohort.
            if config.include_term_variants and config.academic_terms:
                term_clause = _or_clause(config.academic_terms)
                out.append(
                    GeneratedQuery(
                        text=f"site:{domain} {title_clause} {term_clause}",
                        platform_slug=slug,
                        group=group,
                        category=category,
                        priority=weight * 0.8,
                        variant="title_term",
                    ).with_hash()
                )

            # 3. Title + location clause — targets preferred geographies.
            if config.include_location_variants and config.locations:
                loc_clause = _or_clause(config.locations[: config.max_locations_per_clause])
                out.append(
                    GeneratedQuery(
                        text=f"site:{domain} {title_clause} {loc_clause}",
                        platform_slug=slug,
                        group=group,
                        category=category,
                        priority=weight * 0.7,
                        variant="title_location",
                    ).with_hash()
                )

    return out


def select_batch(
    candidates: list[GeneratedQuery],
    batch_size: int,
    rotation: int = 0,
    priority_overrides: dict[str, float] | None = None,
) -> list[GeneratedQuery]:
    """Pick a rotated, priority-weighted batch.

    Strategy: reserve the top third of the batch for the highest-priority
    queries (so high-value combinations run every scan), and fill the rest by
    rotating a window through the remaining queries keyed on `rotation` (e.g. a
    monotonically increasing scan counter) so coverage spreads over time.

    `priority_overrides` maps query_hash -> learned priority (e.g. from historic
    relevant-hit rate) and is layered on top of the static priority.
    """
    if batch_size <= 0 or not candidates:
        return []

    overrides = priority_overrides or {}

    def effective_priority(q: GeneratedQuery) -> float:
        return q.priority + overrides.get(q.query_hash, 0.0)

    ranked = sorted(candidates, key=lambda q: (-effective_priority(q), q.query_hash))

    reserved = max(1, batch_size // 3)
    top = ranked[:reserved]

    rest = ranked[reserved:]
    if not rest:
        return top[:batch_size]

    need = batch_size - len(top)
    start = (rotation * need) % len(rest)
    window: list[GeneratedQuery] = []
    for i in range(min(need, len(rest))):
        window.append(rest[(start + i) % len(rest)])

    return top + window
