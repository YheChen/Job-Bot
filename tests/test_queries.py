from __future__ import annotations

from jobbot.queries.generator import QueryGenConfig, build_queries, select_batch
from jobbot.queries.terms import PLATFORMS


def test_build_queries_produces_site_filtered_queries():
    queries = build_queries(QueryGenConfig(enabled_platforms=["ashby"]))
    assert queries
    for q in queries:
        assert q.text.startswith("site:jobs.ashbyhq.com ")
        assert "(" in q.text and ")" in q.text  # OR-clause (single-title groups have no OR)
        assert q.query_hash  # hashed
    # Multi-title groups produce OR-clauses.
    assert any(" OR " in q.text for q in queries)


def test_build_queries_covers_title_term_and_location_variants():
    queries = build_queries(QueryGenConfig(enabled_platforms=["greenhouse"]))
    variants = {q.variant for q in queries}
    assert {"title", "title_term", "title_location"} <= variants


def test_no_blind_cartesian_explosion():
    # All platforms, all groups, three variants — bounded, not a full product of
    # every title x term x location.
    queries = build_queries()
    max_expected = len(PLATFORMS) * 10 * 3 + 50
    assert len(queries) < max_expected


def test_academic_term_clause_is_quoted():
    queries = build_queries(
        QueryGenConfig(enabled_platforms=["lever"], academic_terms=["Summer 2027"])
    )
    term_q = next(q for q in queries if q.variant == "title_term")
    assert '"Summer 2027"' in term_q.text


def test_select_batch_is_bounded_and_prioritized():
    queries = build_queries()
    batch = select_batch(queries, batch_size=10, rotation=0)
    assert len(batch) == 10
    # Highest static-priority group (generic_swe) should appear in the reserved head.
    assert any(q.group == "generic_swe" for q in batch[:4])


def test_select_batch_rotation_changes_coverage():
    queries = build_queries()
    b0 = {q.query_hash for q in select_batch(queries, 10, rotation=0)}
    b1 = {q.query_hash for q in select_batch(queries, 10, rotation=1)}
    # Reserved head overlaps, but the rotated tail should differ.
    assert b0 != b1


def test_priority_overrides_boost_learned_queries():
    queries = build_queries(QueryGenConfig(enabled_platforms=["icims"]))
    target = queries[-1]  # a low-priority tail query
    boosted = select_batch(
        queries, batch_size=3, rotation=0, priority_overrides={target.query_hash: 10.0}
    )
    assert target.query_hash in {q.query_hash for q in boosted}
