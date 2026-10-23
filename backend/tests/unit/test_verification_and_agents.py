from __future__ import annotations

from datetime import date

from tests.factories import assertion, doc, edge
from truthguard.agents.planner import PlannerAgent, parse_question_date
from truthguard.core.config import Settings
from truthguard.domain.enums import Relation, ResolutionStatus, RuleId
from truthguard.domain.models import Chunk, Resolution
from truthguard.ingestion.lexicon import load_lexicon
from truthguard.llm.cache import ResponseCache
from truthguard.llm.gateway import LLMGateway
from truthguard.verification.blend_detector import classify_value
from truthguard.verification.nli import HeuristicNLI
from truthguard.verification.span_check import locate_quote, verify_quote
from truthguard.verification.staleness import stale_reason

LEX = load_lexicon(Settings().lexicon_path)
OFFLINE = LLMGateway({}, ResponseCache(None), mode="offline")


def test_span_check_is_whitespace_tolerant_but_strict_on_words() -> None:
    text = "A late fee of ₹750 is charged on the Platinum\nCredit Card."
    assert verify_quote("charged on the Platinum Credit Card.", text)
    loc = locate_quote("charged on the Platinum Credit Card.", text)
    assert loc is not None and text[loc[0] : loc[1]].startswith("charged")
    assert not verify_quote("A late fee of ₹950 is charged", text)
    assert not verify_quote("   ", text)


def test_heuristic_nli() -> None:
    nli = HeuristicNLI(LEX)
    ok = nli.score(
        "The late payment fee for the Platinum Credit Card is ₹950.",
        "a charge of ₹950 shall be levied per statement cycle",
        "Amendment > Where the minimum amount due on a Platinum Credit Card is not paid",
    )
    assert ok.entailment >= 0.8 and ok.contradiction == 0
    bad = nli.score(
        "The late payment fee for the Platinum Credit Card is ₹850.",
        "a late payment fee of ₹950 is charged on the Platinum Credit Card",
    )
    assert bad.entailment < 0.6 and bad.contradiction > 0.5


def test_staleness() -> None:
    docs = {
        "v1": doc("v1", eff=date(2023, 1, 1)),
        "v2": doc("v2", eff=date(2024, 3, 1)),
        "fut": doc("fut", eff=date(2027, 1, 1)),
    }
    ch = Chunk(id="c", document_id="v1", ord=0, section_path=["4.2 Late"], text="x", char_start=0, char_end=1)
    assert stale_reason(ch, docs["v1"], docs, [edge("v2", "v1")], date(2026, 1, 1)) is not None
    assert stale_reason(ch, docs["v1"], docs, [edge("v2", "v1")], date(2023, 6, 1)) is None
    ch_fut = ch.model_copy(update={"document_id": "fut"})
    assert "not in force" in (stale_reason(ch_fut, docs["fut"], docs, [], date(2026, 1, 1)) or "")
    scoped = edge("v2", "v1", Relation.AMENDS, scope="4.1")
    assert stale_reason(ch, docs["v1"], docs, [scoped], date(2026, 1, 1)) is None


def test_blend_classifier() -> None:
    items = [
        assertion("old", "v1", "INR:750"),
        assertion("new", "v2", "INR:950"),
        assertion("inj", "x", "INR:0", quarantined=True),
    ]
    res = Resolution(
        conflict_id="c",
        slot_key="s|a",
        status=ResolutionStatus.RESOLVED,
        winner_assertion_ids=["new"],
        eliminated_by={"old": RuleId.R1, "inj": RuleId.QUARANTINE},
        rationale="",
        as_of=date(2026, 1, 1),
    )
    assert classify_value("INR:950", items, res).verdict == "winner"
    stale = classify_value("INR:750", items, res)
    assert stale.verdict == "stale" and stale.rule == RuleId.R1
    assert classify_value("INR:850", items, res).verdict == "blended"
    assert classify_value("INR:0", items, res).verdict == "quarantined"


def test_question_dates() -> None:
    assert parse_question_date("As of 1 March 2027, how often") == date(2027, 3, 1)
    assert parse_question_date("As of June 2024, what late fee") == date(2024, 6, 1)
    assert parse_question_date("What is the fee?") is None


async def test_planner_rules() -> None:
    p = PlannerAgent(LEX, OFFLINE, "m")
    plan = await p.plan(
        "I am self-employed. What minimum income do I need for a personal loan?", date(2026, 10, 23)
    )
    assert [(s.key, s.scope) for s in plan.slots] == [("personal_loan|min_monthly_income", "self_employed")]
    plan = await p.plan(
        "What is the Platinum card late fee, and how many days do I have to report a disputed transaction?",
        date(2026, 10, 23),
    )
    assert {s.key for s in plan.slots} == {
        "platinum_credit_card|late_payment_fee",
        "card_disputes|dispute_window",
    }
    assert plan.intent == "compare"
    plan = await p.plan("What is the minimum balance for an NRI account?", date(2026, 10, 23))
    assert plan.slots == [] and plan.unknown_entities == ["NRI account"]
    plan = await p.plan("I read the Platinum late fee is ₹500, right?", date(2026, 10, 23))
    assert plan.claimed_values == ["INR:500"]
    plan = await p.plan(
        "What was the FD rate on a 1-year deposit as of 1 March 2025?",
        date(2026, 10, 23),
        explicit_as_of=True,
    )
    assert plan.as_of == date(2026, 10, 23)  # an explicit API as-of wins over the question text
