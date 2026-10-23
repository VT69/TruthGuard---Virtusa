"""One test per precedence rule, plus the edge cases the demo relies on."""

from __future__ import annotations

from datetime import date

import pytest

from tests.factories import assertion, doc, edge
from truthguard.core.config import Settings
from truthguard.domain.enums import Relation, ResolutionStatus, RuleId
from truthguard.resolution.engine import FLAG_INFERRED_EDGE, FLAG_UNOFFICIAL, Resolver
from truthguard.resolution.policy import PrecedencePolicy, load_policy

TODAY = date(2026, 10, 23)


@pytest.fixture
def policy() -> PrecedencePolicy:
    return load_policy(Settings().precedence_path)


def run(policy: PrecedencePolicy, assertions, docs, edges=(), as_of=TODAY, scope=None):  # type: ignore[no-untyped-def]
    return Resolver(policy).resolve(
        "c", "s|a", list(assertions), {d.id: d for d in docs}, list(edges), as_of, scope
    )


def test_no_conflict(policy: PrecedencePolicy) -> None:
    r = run(policy, [assertion("a", "d1", "INR:1"), assertion("b", "d2", "INR:1")], [doc("d1"), doc("d2")])
    assert r.status == ResolutionStatus.NO_CONFLICT
    assert r.deciding_rule is None and r.strength == 1.0


def test_r0_temporal_validity_excludes_future_document(policy: PrecedencePolicy) -> None:
    docs = [doc("old", eff=date(2024, 1, 1)), doc("future", eff=date(2027, 1, 1))]
    r = run(policy, [assertion("a", "old", "YEARS:2"), assertion("b", "future", "YEARS:1")], docs)
    assert r.status == ResolutionStatus.RESOLVED
    assert r.deciding_rule == RuleId.R0
    assert r.eliminated_by == {"b": RuleId.R0}
    # time travel: after the effective date, the future document wins by supersession
    r2 = run(
        policy,
        [assertion("a", "old", "YEARS:2"), assertion("b", "future", "YEARS:1")],
        docs,
        [edge("future", "old")],
        as_of=date(2027, 3, 1),
    )
    assert r2.winner_assertion_ids == ["b"] and r2.eliminated_by == {"a": RuleId.R1}


def test_r1_explicit_supersession_with_clause_scope(policy: PrecedencePolicy) -> None:
    docs = [doc("v2", eff=date(2024, 3, 1)), doc("amend", eff=date(2025, 3, 15))]
    a_in = assertion("in", "v2", "INR:750", section=["Policy", "4. Fees", "4.2 Late Payment Fee"])
    b = assertion("new", "amend", "INR:950")
    r = run(policy, [a_in, b], docs, [edge("amend", "v2", Relation.AMENDS, scope="4.2")])
    assert r.status == ResolutionStatus.RESOLVED and r.deciding_rule == RuleId.R1
    assert r.winner_assertion_ids == ["new"]
    # an assertion outside the amended clause is NOT superseded by it
    a_out = assertion("out", "v2", "INR:750", section=["Policy", "4. Fees", "4.1 Annual Fee"])
    r2 = run(policy, [a_out, b], docs, [edge("amend", "v2", Relation.AMENDS, scope="4.2")])
    assert "out" not in r2.eliminated_by or r2.eliminated_by["out"] != RuleId.R1


def test_r1_transitive_chain(policy: PrecedencePolicy) -> None:
    docs = [doc("c1", eff=date(2025, 1, 1)), doc("c2", eff=date(2025, 7, 1)), doc("c3", eff=date(2026, 1, 1))]
    items = [
        assertion("a", "c1", "PCT:6.8"),
        assertion("b", "c2", "PCT:7.1"),
        assertion("c", "c3", "PCT:7.25"),
    ]
    r = run(policy, items, docs, [edge("c2", "c1"), edge("c3", "c2")])
    assert r.winner_assertion_ids == ["c"]
    assert r.eliminated_by == {"a": RuleId.R1, "b": RuleId.R1}


def test_r1_inferred_edge_lowers_strength(policy: PrecedencePolicy) -> None:
    docs = [doc("old"), doc("new")]
    r = run(
        policy,
        [assertion("a", "old", "INR:500"), assertion("b", "new", "INR:300")],
        docs,
        [edge("new", "old", confidence=0.72)],
    )
    assert r.status == ResolutionStatus.RESOLVED
    assert FLAG_INFERRED_EDGE in r.flags and r.strength <= 0.8


def test_r2_authority_older_lower_tier_loses(policy: PrecedencePolicy) -> None:
    docs = [doc("policy", tier=2, eff=date(2024, 3, 1)), doc("faq", tier=4, pub=date(2023, 5, 1))]
    r = run(policy, [assertion("p", "policy", "INR:1500"), assertion("f", "faq", "INR:1000")], docs)
    assert r.deciding_rule == RuleId.R2 and r.winner_assertion_ids == ["p"]
    assert r.strength == 0.75


def test_r2_newer_lower_tier_escalates_to_unresolved(policy: PrecedencePolicy) -> None:
    docs = [doc("policy", tier=2, eff=date(2024, 2, 1)), doc("memo", tier=5, pub=date(2025, 9, 10))]
    r = run(policy, [assertion("p", "policy", "DAYS:30"), assertion("m", "memo", "DAYS:60")], docs)
    assert r.status == ResolutionStatus.UNRESOLVED
    assert FLAG_UNOFFICIAL in r.flags and r.deciding_rule == RuleId.R2
    assert r.winner_assertion_ids == []


def test_r2_agreeing_lower_tier_is_kept(policy: PrecedencePolicy) -> None:
    docs = [
        doc("policy", tier=2, eff=date(2024, 1, 1)),
        doc("faq", tier=4, pub=date(2025, 1, 1)),
        doc("memo", tier=5, pub=date(2023, 1, 1)),
    ]
    r = run(
        policy,
        [
            assertion("p", "policy", "DAYS:30"),
            assertion("f", "faq", "DAYS:30"),
            assertion("m", "memo", "DAYS:60"),
        ],
        docs,
    )
    assert r.status == ResolutionStatus.RESOLVED
    assert r.eliminated_by == {"m": RuleId.R2}


def test_r3_specificity_with_requested_scope(policy: PrecedencePolicy) -> None:
    docs = [doc("policy", eff=date(2024, 7, 1)), doc("addendum", eff=date(2024, 9, 1))]
    items = [
        assertion("g", "policy", "INR:25000"),
        assertion("s", "addendum", "INR:40000", scope="self_employed"),
    ]
    r = run(policy, items, docs, scope="self_employed")
    assert r.winner_assertion_ids == ["s"] and r.eliminated_by == {"g": RuleId.R3}


def test_r3_scope_split_without_requested_scope(policy: PrecedencePolicy) -> None:
    docs = [
        doc("policy", eff=date(2024, 7, 1)),
        doc("addendum", eff=date(2024, 9, 1)),
        doc("sheet", tier=3, eff=date(2024, 8, 1)),
    ]
    items = [
        assertion("g", "policy", "MONTHS:60"),
        assertion("s", "addendum", "MONTHS:48", scope="self_employed"),
        assertion("x", "sheet", "MONTHS:60"),
    ]
    r = run(policy, items, docs)
    assert r.status == ResolutionStatus.SCOPE_SPLIT
    assert r.winners_by_scope == {"all": "g", "self_employed": "s"}


def test_r4_recency_in_series(policy: PrecedencePolicy) -> None:
    docs = [
        doc("faq23", tier=4, pub=date(2023, 6, 1), series="rewards"),
        doc("faq25", tier=4, pub=date(2025, 5, 1), series="rewards"),
    ]
    r = run(policy, [assertion("a", "faq23", "COUNT:2"), assertion("b", "faq25", "COUNT:3")], docs)
    assert r.deciding_rule == RuleId.R4 and r.winner_assertion_ids == ["b"]
    assert "inferred_supersession" in r.flags and r.strength == 0.55


def test_r5_genuine_tie(policy: PrecedencePolicy) -> None:
    docs = [doc("faq_a", tier=4), doc("faq_b", tier=4)]
    r = run(policy, [assertion("a", "faq_a", "INR:5000"), assertion("b", "faq_b", "INR:10000")], docs)
    assert r.status == ResolutionStatus.UNRESOLVED and r.deciding_rule == RuleId.R5
    assert r.rule_chain[-1].rule == RuleId.R5


def test_quarantined_assertions_never_win(policy: PrecedencePolicy) -> None:
    docs = [doc("policy", eff=date(2025, 3, 15)), doc("offers", tier=4, pub=date(2025, 8, 1))]
    r = run(
        policy,
        [assertion("p", "policy", "INR:950"), assertion("x", "offers", "INR:0", quarantined=True)],
        docs,
    )
    assert r.eliminated_by == {"x": RuleId.QUARANTINE}
    assert r.status == ResolutionStatus.NO_CONFLICT and r.winner_assertion_ids == ["p"]


def test_policy_recency_over_authority_flips_verdict(policy: PrecedencePolicy) -> None:
    docs = [doc("policy", tier=2, eff=date(2024, 2, 1)), doc("memo", tier=5, pub=date(2025, 9, 10))]
    items = [assertion("p", "policy", "DAYS:30"), assertion("m", "memo", "DAYS:60")]
    flipped = policy.model_copy(
        update={
            "order": [RuleId.R0, RuleId.R1, RuleId.R4, RuleId.R2, RuleId.R3, RuleId.R5],
            "rules": policy.rules.model_copy(
                update={"R4": policy.rules.R4.model_copy(update={"cross_series": True})}
            ),
        }
    )
    r = run(flipped, items, docs)
    assert r.status == ResolutionStatus.RESOLVED and r.winner_assertion_ids == ["m"]
    assert r.deciding_rule == RuleId.R4


def test_rule_chain_logs_every_evaluated_rule(policy: PrecedencePolicy) -> None:
    docs = [doc("faq_a", tier=4), doc("faq_b", tier=4)]
    r = run(policy, [assertion("a", "faq_a", "INR:5"), assertion("b", "faq_b", "INR:6")], docs)
    assert [e.rule for e in r.rule_chain] == [
        RuleId.R0,
        RuleId.R1,
        RuleId.R2,
        RuleId.R3,
        RuleId.R4,
        RuleId.R5,
    ]


def test_policy_validation() -> None:
    with pytest.raises(ValueError):
        PrecedencePolicy(order=[RuleId.R5, RuleId.R0])
    with pytest.raises(ValueError):
        PrecedencePolicy(order=[RuleId.R0, RuleId.R0])
    with pytest.raises(ValueError):
        PrecedencePolicy(tiers={"faq": 0})
