"""End-to-end agent graph: all four statuses, time travel, and the critic loop with a
(fake) live LLM that drafts a bad answer first."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from tests.conftest import CORPUS, make_settings
from truthguard.domain.enums import AnswerStatus, ObjectionType, RuleId
from truthguard.llm.providers import FakeProvider, ProviderRequest
from truthguard.resolution.policy import PrecedencePolicy
from truthguard.services.container import AppContainer


async def ask(c: AppContainer, q: str, as_of: date | None = None):  # type: ignore[no-untyped-def]
    out = await c.ask(q, as_of)
    assert out.final is not None
    return out.final


async def test_resolved_conflict_late_fee(seeded: AppContainer) -> None:
    f = await ask(seeded, "What is the late payment fee on the Platinum Credit Card?")
    assert f.status == AnswerStatus.RESOLVED_CONFLICT
    assert any(c.value_norm == "INR:950" and c.verdict == "winner" for c in f.claims)
    card = f.conflicts[0]
    eliminated = {p.document_id: p.eliminated_by for p in card.positions if p.outcome == "eliminated"}
    assert eliminated["card_fees_policy_v2"] == RuleId.R1
    assert eliminated["help_faq_cards_2023"] == RuleId.R2
    assert f.injection_flags == ["platinum_offers_update_2025"]
    assert all(cit.verified for c in f.claims for cit in c.citations)
    assert "₹0" not in f.answer_text


async def test_unresolved_authority_clash(seeded: AppContainer) -> None:
    f = await ask(seeded, "How many days do customers have to report a disputed transaction?")
    assert f.status == AnswerStatus.UNRESOLVED_CONFLICT
    assert "possible_unofficial_update" in f.conflicts[0].resolution.flags
    assert {c.value_norm for c in f.claims if c.verdict == "position"} == {"DAYS:30", "DAYS:60"}
    assert f.conflicts[0].who_should_decide == "Risk Committee"


async def test_verified_numeric_drift(seeded: AppContainer) -> None:
    f = await ask(seeded, "What is the foreign currency mark-up on the Platinum Credit Card?")
    assert f.status == AnswerStatus.VERIFIED and not f.conflicts
    assert f.claims[0].value_norm == "PCT:1.5"


async def test_insufficient_evidence(seeded: AppContainer) -> None:
    f = await ask(seeded, "What is the annual fee for the Titanium Credit Card?")
    assert f.status == AnswerStatus.INSUFFICIENT_EVIDENCE and not f.claims


async def test_scope_split(seeded: AppContainer) -> None:
    f = await ask(seeded, "What is the minimum income required for a personal loan?")
    assert f.status == AnswerStatus.RESOLVED_CONFLICT
    assert {c.value_norm for c in f.claims if c.verdict == "winner"} == {"INR:25000", "INR:40000"}


async def test_as_of_in_question_and_time_travel(seeded: AppContainer) -> None:
    f = await ask(seeded, "As of 1 March 2025, what was the interest rate on a 1-year fixed deposit?")
    assert f.as_of == date(2025, 3, 1)
    assert any(c.value_norm == "PCT:6.8" and c.verdict == "winner" for c in f.claims)
    # re-resolve the same trace at other dates: no LLM, no retrieval
    later = await seeded.re_resolve(f.trace_id, date(2026, 2, 1), None)
    assert any(c.value_norm == "PCT:7.25" and c.verdict == "winner" for c in later.claims)
    mid = await seeded.re_resolve(f.trace_id, date(2025, 9, 1), None)
    assert any(c.value_norm == "PCT:7.1" and c.verdict == "winner" for c in mid.claims)
    assert mid.usage.latency_ms < 500


async def test_policy_override_flips_verdict(seeded: AppContainer) -> None:
    f = await ask(seeded, "What is the window for raising a card dispute?")
    assert f.status == AnswerStatus.UNRESOLVED_CONFLICT
    p = seeded.policy
    flipped = PrecedencePolicy.model_validate(
        {
            **p.model_dump(mode="json"),
            "order": ["R0", "R1", "R4", "R2", "R3", "R5"],
            "rules": {"R2": {"escalate_if_lower_tier_newer": True}, "R4": {"cross_series": True}},
        }
    )
    g = await seeded.re_resolve(f.trace_id, None, flipped)
    assert g.status == AnswerStatus.RESOLVED_CONFLICT
    assert any(c.value_norm == "DAYS:60" and c.verdict == "winner" for c in g.claims)


async def test_trace_events_cover_every_node(seeded: AppContainer) -> None:
    events = []

    async def sink(ev):  # type: ignore[no-untyped-def]
        events.append(ev.event)

    await seeded.ask("What is the Gold Credit Card annual fee?", sink=sink)
    for name in (
        "plan.created",
        "retrieval.completed",
        "evidence.analyzed",
        "conflict.detected",
        "resolution.applied",
        "draft.created",
        "critic.completed",
        "answer.final",
    ):
        assert name in events


async def test_critic_loop_rejects_bad_llm_draft_then_accepts_revision(tmp_path: Path) -> None:
    """Live mode with a fake LLM: the first synthesizer draft blends values and fabricates a
    quote; the critic objects; the revision is correct and gets finalized."""
    drafts = iter(
        [
            {
                "summary": "The late fee is ₹850.",
                "disclosed_conflicts": [],
                "claims": [
                    {
                        "text": "The late payment fee for the Platinum Credit Card is ₹850.",
                        "slot_key": "platinum_credit_card|late_payment_fee",
                        "value_text": "₹850",
                        "verdict": "winner",
                        "citations": [
                            {"chunk_id": "PLACEHOLDER", "quote": "a charge of ₹850 shall be levied"}
                        ],
                    }
                ],
            },
            {
                "summary": "The late payment fee for the Platinum Credit Card is ₹950; older sources are superseded.",
                "disclosed_conflicts": ["platinum_credit_card|late_payment_fee"],
                "claims": [
                    {
                        "text": "The late payment fee for the Platinum Credit Card is ₹950.",
                        "slot_key": "platinum_credit_card|late_payment_fee",
                        "value_text": "₹950",
                        "verdict": "winner",
                        "citations": [
                            {
                                "chunk_id": "PLACEHOLDER",
                                "quote": "a charge of ₹950 shall be levied per statement cycle",
                            }
                        ],
                    }
                ],
            },
        ]
    )
    amend_chunk: dict[str, str] = {}

    def handler(req: ProviderRequest) -> str:
        system = req.messages[0]["content"]
        if "You draft the answer" in system:
            d = json.dumps(next(drafts)).replace("PLACEHOLDER", amend_chunk["id"])
            return d
        if "query planner" in system:
            return json.dumps(
                {
                    "intent": "lookup",
                    "slots": [{"subject_id": "platinum_credit_card", "attribute": "late_payment_fee"}],
                }
            )
        if "fact checker" in system:
            return json.dumps({"judgements": []})
        if "Rewrite the draft" in system:
            return json.dumps({"text": "The Platinum late fee is ₹950."})
        return json.dumps({"assertions": []})

    fake = {
        "groq": FakeProvider(name="groq", handler=handler),
        "gemini": FakeProvider(name="gemini", handler=handler),
    }
    c = AppContainer(make_settings(tmp_path, llm_mode="offline"), providers=fake)  # ingest offline (rules)
    await c.startup()
    await c.seed(CORPUS)
    amend_chunk["id"] = next(a.chunk_id for a in c.corpus.assertions.values() if a.value_norm == "INR:950")
    c.gateway.mode = "live"
    out = await c.ask("What is the late payment fee on the Platinum Credit Card?")
    st = out.state
    assert st is not None and out.final is not None
    first = {o.type for o in st.reports[0].objections}
    assert ObjectionType.FABRICATED_QUOTE in first
    assert ObjectionType.BLENDED_VALUE in first
    assert ObjectionType.UNDISCLOSED_CONFLICT in first
    assert st.rounds == 1
    assert not [o for o in st.reports[-1].objections if o.blocking]
    assert out.final.status == AnswerStatus.RESOLVED_CONFLICT
    assert out.final.claims[0].value_norm == "INR:950"
    assert out.final.usage.llm_calls >= 4
    await c.shutdown()


async def test_challenge_mode_new_memo_creates_conflict(tmp_path: Path) -> None:
    from truthguard.ingestion.pipeline import Overrides

    c = AppContainer(make_settings(tmp_path))
    await c.startup()
    await c.seed(CORPUS)
    memo = (
        "# Card fee update\n\nEffective today, the late payment fee on the Platinum Credit Card is ₹1,200.\n"
    )
    await c.ingest(
        [("judge_memo.md", memo.encode())],
        origin="user",
        overrides=Overrides(title="Card fee update", doc_type="memo", published_at=date(2026, 10, 23)),
    )
    f = (await c.ask("What is the late payment fee on the Platinum Credit Card?")).final
    assert f is not None
    assert f.status == AnswerStatus.UNRESOLVED_CONFLICT
    assert "possible_unofficial_update" in f.conflicts[0].resolution.flags
    assert "INR:1200" in {p.value_norm for p in f.conflicts[0].positions}
    removed = await c.reset()
    assert removed["removed_documents"] == 1
    f2 = (await c.ask("What is the late payment fee on the Platinum Credit Card?")).final
    assert f2 is not None and f2.status == AnswerStatus.RESOLVED_CONFLICT
    await c.shutdown()


async def test_baseline_blend_overlay(seeded: AppContainer) -> None:
    out = await seeded.ask("What is the interest rate on a 1-year fixed deposit?", mode="B0")
    b = out.baseline
    assert b is not None and b.status == AnswerStatus.VERIFIED
    assert b.value_flags and b.value_flags[0].verdict in ("stale", "winner")
