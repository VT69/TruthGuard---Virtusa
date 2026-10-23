"""Property tests for the resolver: determinism, input-order independence, rule precedence."""

from __future__ import annotations

from datetime import date, timedelta

from hypothesis import given, settings
from hypothesis import strategies as st

from tests.factories import assertion, doc, edge
from truthguard.core.config import Settings
from truthguard.domain.enums import ResolutionStatus, RuleId
from truthguard.resolution.engine import Resolver
from truthguard.resolution.policy import load_policy

POLICY = load_policy(Settings().precedence_path)
AS_OF = date(2026, 10, 23)


@st.composite
def scenarios(draw):  # type: ignore[no-untyped-def]
    n = draw(st.integers(min_value=1, max_value=6))
    docs, items = [], []
    for i in range(n):
        tier = draw(st.integers(min_value=1, max_value=5))
        offset = draw(st.integers(min_value=-1500, max_value=400))
        dated = draw(st.booleans())
        series = draw(st.sampled_from(["s1", "s2", f"own{i}"]))
        d = doc(f"d{i}", tier=tier, eff=AS_OF + timedelta(days=offset) if dated else None, series=series)
        docs.append(d)
        value = draw(st.sampled_from(["INR:1", "INR:2", "INR:3"]))
        scope = draw(st.sampled_from(["all", "all", "x"]))
        items.append(assertion(f"a{i}", d.id, value, scope=scope, quarantined=draw(st.booleans()) and i > 0))
    edges = []
    for i in range(n):
        for j in range(n):
            if i != j and draw(st.integers(0, 9)) == 0:
                edges.append(edge(f"d{i}", f"d{j}"))
    return docs, items, edges


def _resolve(docs, items, edges):  # type: ignore[no-untyped-def]
    return Resolver(POLICY).resolve("c", "s|a", items, {d.id: d for d in docs}, edges, AS_OF)


@settings(max_examples=300, deadline=None)
@given(scenarios())
def test_deterministic(scn) -> None:  # type: ignore[no-untyped-def]
    docs, items, edges = scn
    assert _resolve(docs, items, edges) == _resolve(docs, items, edges)


@settings(max_examples=300, deadline=None)
@given(scenarios(), st.randoms())
def test_input_order_independent(scn, rnd) -> None:  # type: ignore[no-untyped-def]
    docs, items, edges = scn
    a = _resolve(docs, items, edges)
    shuffled_items, shuffled_edges = list(items), list(edges)
    rnd.shuffle(shuffled_items)
    rnd.shuffle(shuffled_edges)
    b = _resolve(docs, shuffled_items, shuffled_edges)
    assert a.status == b.status
    assert sorted(a.winner_assertion_ids) == sorted(b.winner_assertion_ids)
    assert a.eliminated_by == b.eliminated_by
    assert a.deciding_rule == b.deciding_rule


@settings(max_examples=300, deadline=None)
@given(scenarios())
def test_winners_survive_and_are_consistent(scn) -> None:  # type: ignore[no-untyped-def]
    docs, items, edges = scn
    r = _resolve(docs, items, edges)
    assert not set(r.winner_assertion_ids) & set(r.eliminated_by)
    for a in items:
        if a.quarantined:
            assert r.eliminated_by.get(a.id) == RuleId.QUARANTINE
    if r.status == ResolutionStatus.RESOLVED:
        by_id = {a.id: a for a in items}
        values_by_scope: dict[str, set[str]] = {}
        for w in r.winner_assertion_ids:
            values_by_scope.setdefault(by_id[w].scope, set()).add(by_id[w].value_norm)
        assert all(len(v) == 1 for v in values_by_scope.values())


@settings(max_examples=200, deadline=None)
@given(st.integers(min_value=1, max_value=4), st.integers(min_value=1, max_value=900))
def test_higher_precedence_rule_beats_lower(tier_gap: int, days_newer: int) -> None:
    """An explicit supersession (R1) always beats authority (R2) and recency (R4): the
    superseding document wins even when it has LOWER authority and is OLDER."""
    old = doc("old", tier=1, eff=AS_OF - timedelta(days=10), series="s")
    new = doc("new", tier=1 + tier_gap, eff=AS_OF - timedelta(days=10 + days_newer), series="s")
    r = _resolve(
        [old, new], [assertion("o", "old", "INR:1"), assertion("n", "new", "INR:2")], [edge("new", "old")]
    )
    assert r.winner_assertion_ids == ["n"]
    assert r.eliminated_by == {"o": RuleId.R1}


@settings(max_examples=200, deadline=None)
@given(st.integers(min_value=1, max_value=4), st.integers(min_value=1, max_value=900))
def test_authority_beats_recency_by_default(tier_gap: int, days: int) -> None:
    hi = doc("hi", tier=1, eff=AS_OF - timedelta(days=10), series="s")
    lo = doc("lo", tier=1 + tier_gap, eff=AS_OF - timedelta(days=10 + days), series="s")
    r = _resolve([hi, lo], [assertion("h", "hi", "INR:1"), assertion("l", "lo", "INR:2")], [])
    assert r.winner_assertion_ids == ["h"] and r.eliminated_by == {"l": RuleId.R2}
