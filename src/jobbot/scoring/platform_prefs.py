"""Platform preference tiers.

Not all ATSes are equally pleasant to apply through. Ashby/Greenhouse/Lever
take a couple of minutes; Workday and Oracle typically demand an account, a
long multi-page form, and re-typing a résumé that was just uploaded. Two
otherwise identical postings are therefore not equally valuable, so the
scorer grades the hosting platform instead of treating every ATS alike.

Tiers are advisory, not filters: a deprioritized posting still gets scored,
posted, and deduped — it just sorts below an equivalent preferred one. To
stop searching a platform entirely use /jobs disable-platform.
"""

from __future__ import annotations

# Quick, single-page applications.
DEFAULT_PREFERRED_PLATFORMS: tuple[str, ...] = (
    "ashby",
    "greenhouse",
    "lever",
    "workable",
    "smartrecruiters",
)

# Account creation and/or long multi-step forms.
DEFAULT_DEPRIORITIZED_PLATFORMS: tuple[str, ...] = (
    "workday",
    "oracle",
    "successfactors",
    "icims",
    "adp",
)

# Multiplier applied to the platform-preference weight.
_PREFERRED = 1.0
_NEUTRAL = 0.5  # a recognized ATS that is on neither list
_DEPRIORITIZED = 0.0

# Not a real ATS (company career page, unknown host): no preference signal.
_NON_ATS_SLUGS = frozenset({"company", "generic"})


def is_direct_ats(platform_slug: str | None) -> bool:
    """True when the link goes straight to a recognized applicant tracking system."""
    return bool(platform_slug) and platform_slug not in _NON_ATS_SLUGS


def platform_factor(
    platform_slug: str | None,
    preferred: list[str] | tuple[str, ...] | None = None,
    deprioritized: list[str] | tuple[str, ...] | None = None,
) -> float:
    """Return 0..1 for how preferred a platform is.

    Precedence, so an explicit choice is never silently ignored:
      1. slugs the caller listed explicitly (deprioritizing "ashby" works even
         though it ships in the default preferred tier)
      2. the built-in tier, but only for a list the caller left empty — passing
         a preferred list replaces the default preferred tier rather than
         adding to it
      3. neutral

    A slug appearing in both caller lists is treated as preferred.
    """
    if not is_direct_ats(platform_slug):
        return 0.0

    slug = (platform_slug or "").lower()
    # Normalize the registry's split slugs (greenhouse_jb, workday_alt, ...)
    # so callers can just say "greenhouse" or "workday".
    base = slug.split("_", 1)[0]

    def _matches(names: set[str]) -> bool:
        return slug in names or base in names

    explicit_preferred = {p.lower() for p in (preferred or ())}
    explicit_deprioritized = {p.lower() for p in (deprioritized or ())}

    if _matches(explicit_preferred):
        return _PREFERRED
    if _matches(explicit_deprioritized):
        return _DEPRIORITIZED

    if not explicit_preferred and _matches({p.lower() for p in DEFAULT_PREFERRED_PLATFORMS}):
        return _PREFERRED
    if not explicit_deprioritized and _matches(
        {p.lower() for p in DEFAULT_DEPRIORITIZED_PLATFORMS}
    ):
        return _DEPRIORITIZED
    return _NEUTRAL
