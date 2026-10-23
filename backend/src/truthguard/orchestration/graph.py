"""The query-time agent graph: a small, explicit async state machine (ADR 0003).

    plan -> retrieve -> analyze -> resolve -> synthesize -> critique --(route)--> finalize
                           ^                       ^             |
                           |  NEW_CONFLICT         |  revise     |
                           +-----------------------+-------------+

Routing after the critic: no blocking objection -> finalize; NEW_CONFLICT -> back to the
analyst with the counter-retrieved evidence; other blocking objections -> the synthesizer
revises with the objections attached. At most `max_rounds` revisions, and the loop stops
early when the number of blocking objections stops decreasing.

Every node emits a TraceEvent (streamed over SSE and persisted), carrying per-node tokens,
cost and latency, so the trace viewer can replay the debate.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from truthguard.agents.analyst import AnalystAgent
from truthguard.agents.critic import CriticAgent
from truthguard.agents.finalizer import FinalizerAgent
from truthguard.agents.planner import PlannerAgent
from truthguard.agents.synthesizer import SynthesizerAgent
from truthguard.core.clock import now
from truthguard.domain.enums import ObjectionType
from truthguard.domain.models import (
    CriticReport,
    DraftAnswer,
    EvidenceSet,
    FinalAnswer,
    Objection,
    QueryPlan,
    Resolution,
    SlotAnalysis,
    TraceEvent,
    Usage,
)
from truthguard.llm.cost import UsageMeter, current_meter, current_node
from truthguard.resolution.engine import Resolver
from truthguard.retrieval.retriever import Retriever

EventSink = Callable[[TraceEvent], Awaitable[None]]
ResolveFn = Callable[[SlotAnalysis, EvidenceSet, date], Awaitable[Resolution]]


@dataclass
class GraphOptions:
    lineage_expansion: bool = True
    counter_retrieval: bool = True
    max_rounds: int = 2
    top_k: int | None = None  # retrieval budget override (eval stress test)
    resolve_override: ResolveFn | None = None  # ablation A3 only


@dataclass
class GraphState:
    trace_id: str
    question: str
    as_of: date
    plan: QueryPlan | None = None
    evidence: EvidenceSet = field(default_factory=EvidenceSet)
    analyses: list[SlotAnalysis] = field(default_factory=list)
    draft: DraftAnswer | None = None
    drafts: list[DraftAnswer] = field(default_factory=list)
    reports: list[CriticReport] = field(default_factory=list)
    rounds: int = 0
    final: FinalAnswer | None = None
    events: list[TraceEvent] = field(default_factory=list)
    started: float = field(default_factory=time.perf_counter)

    def snapshot(self) -> dict[str, Any]:
        """What re-resolve (time travel / policy edit) needs: plan + evidence + analyses."""
        return {
            "plan": self.plan.model_dump(mode="json") if self.plan else None,
            "evidence": self.evidence.model_dump(mode="json"),
            "analyses": [a.model_dump(mode="json") for a in self.analyses],
            "drafts": [d.model_dump(mode="json") for d in self.drafts],
            "reports": [r.model_dump(mode="json") for r in self.reports],
        }


class TruthGuardGraph:
    def __init__(
        self,
        planner: PlannerAgent,
        retriever: Retriever,
        analyst: AnalystAgent,
        synthesizer: SynthesizerAgent,
        critic: CriticAgent,
        finalizer: FinalizerAgent,
    ) -> None:
        self.planner = planner
        self.retriever = retriever
        self.analyst = analyst
        self.synthesizer = synthesizer
        self.critic = critic
        self.finalizer = finalizer

    async def run(
        self,
        state: GraphState,
        resolver: Resolver,
        sink: EventSink | None = None,
        options: GraphOptions | None = None,
        explicit_as_of: bool = False,
    ) -> GraphState:
        opts = options or GraphOptions()
        meter = UsageMeter()
        token = current_meter.set(meter)
        seq = 0

        async def emit(event: str, node: str, payload: dict[str, Any], t0: float) -> None:
            nonlocal seq
            u = meter.by_node.get(node, Usage())
            ev = TraceEvent(
                trace_id=state.trace_id,
                seq=seq,
                event=event,
                node=node,
                ts=now(),
                payload=payload,
                tokens=u.prompt_tokens + u.completion_tokens,
                cost_usd=round(u.cost_usd, 6),
                latency_ms=round((time.perf_counter() - t0) * 1000, 2),
            )
            seq += 1
            state.events.append(ev)
            if sink is not None:
                await sink(ev)

        try:
            # planner ---------------------------------------------------------------------
            t0 = time.perf_counter()
            current_node.set("planner")
            plan = await self.planner.plan(state.question, state.as_of, explicit_as_of)
            state.plan = plan
            state.as_of = plan.as_of
            await emit("plan.created", "planner", plan.model_dump(mode="json"), t0)

            # retriever -------------------------------------------------------------------
            t0 = time.perf_counter()
            current_node.set("retriever")
            state.evidence = await self.retriever.retrieve(
                plan, lineage_expansion=opts.lineage_expansion, top_k=opts.top_k
            )
            await emit("retrieval.completed", "retriever", self._evidence_payload(state.evidence), t0)

            await self._analyze_and_resolve(state, resolver, opts, emit)

            objections: list[Objection] = []
            prev_blocking: int | None = None
            while True:
                # synthesizer ---------------------------------------------------------
                t0 = time.perf_counter()
                current_node.set("synthesizer")
                draft = await self.synthesizer.draft(
                    plan, state.evidence, state.analyses, objections, state.rounds
                )
                state.draft = draft
                state.drafts.append(draft)
                await emit("draft.created", "synthesizer", draft.model_dump(mode="json"), t0)

                # critic --------------------------------------------------------------
                t0 = time.perf_counter()
                current_node.set("critic")
                report, new_items = await self.critic.review(
                    plan,
                    draft,
                    state.evidence,
                    state.analyses,
                    allow_counter=opts.counter_retrieval and state.rounds < opts.max_rounds,
                )
                state.reports.append(report)
                for o in report.objections:
                    await emit("critic.objection", "critic", o.model_dump(mode="json"), t0)
                await emit(
                    "critic.completed",
                    "critic",
                    {
                        "checks": [c.model_dump(mode="json") for c in report.checks],
                        "counter_queries": report.counter_queries,
                        "new_sources": [
                            {
                                "chunk_id": it.chunk.id,
                                "document_id": it.document.id,
                                "title": it.document.title,
                            }
                            for it in new_items
                        ],
                        "blocking": sum(1 for o in report.objections if o.blocking),
                    },
                    t0,
                )

                blocking = [o for o in report.objections if o.blocking]
                objections = report.objections
                if not blocking:
                    break
                if state.rounds >= opts.max_rounds:
                    break
                if prev_blocking is not None and len(blocking) >= prev_blocking:
                    break  # objections stopped decreasing
                prev_blocking = len(blocking)
                state.rounds += 1
                new_conflict = any(o.type == ObjectionType.NEW_CONFLICT and o.blocking for o in blocking)
                t0 = time.perf_counter()
                await emit(
                    "revision.started",
                    "router",
                    {
                        "round": state.rounds,
                        "route": "re-analyze" if new_conflict else "revise",
                        "reasons": [o.type.value for o in blocking],
                    },
                    t0,
                )
                if new_conflict and new_items:
                    state.evidence = state.evidence.model_copy(
                        update={
                            "items": [*state.evidence.items, *new_items],
                            "edges": self._edges_for(state.evidence, new_items),
                        }
                    )
                    await self._analyze_and_resolve(state, resolver, opts, emit)

            # finalizer -------------------------------------------------------------------
            t0 = time.perf_counter()
            current_node.set("finalizer")
            residual = state.reports[-1].objections if state.reports else []
            usage = meter.snapshot(latency_ms=round((time.perf_counter() - state.started) * 1000, 2))
            assert state.draft is not None
            final = await self.finalizer.finalize(
                state.trace_id,
                plan,
                state.evidence,
                state.analyses,
                state.draft,
                residual,
                state.rounds,
                usage,
            )
            final = final.model_copy(
                update={
                    "usage": meter.snapshot(latency_ms=round((time.perf_counter() - state.started) * 1000, 2))
                }
            )
            state.final = final
            await emit("answer.final", "finalizer", final.model_dump(mode="json"), t0)
        except Exception as exc:
            await emit(
                "error", "graph", {"message": str(exc)[:500], "type": type(exc).__name__}, time.perf_counter()
            )
            raise
        finally:
            current_meter.reset(token)
        return state

    async def _analyze_and_resolve(
        self,
        state: GraphState,
        resolver: Resolver,
        opts: GraphOptions,
        emit: Callable[[str, str, dict[str, Any], float], Awaitable[None]],
    ) -> None:
        assert state.plan is not None
        t0 = time.perf_counter()
        current_node.set("analyst")
        state.analyses = self.analyst.analyze(state.plan, state.evidence)
        by_id = state.evidence.assertion_by_id()
        await emit(
            "evidence.analyzed",
            "analyst",
            {
                "slots": [
                    {
                        "key": a.key,
                        "slot_key": a.row.slot_key,
                        "scope": a.row.requested_scope,
                        "assertions": len(a.row.assertion_ids),
                        "values": sorted({by_id[i].value_display for i in a.row.assertion_ids if i in by_id}),
                        "quarantined": a.quarantined_ids,
                    }
                    for a in state.analyses
                ],
            },
            t0,
        )
        for a in state.analyses:
            if a.conflict is not None:
                await emit("conflict.detected", "analyst", a.conflict.model_dump(mode="json"), t0)

        t0 = time.perf_counter()
        current_node.set("resolver")
        state.analyses = await self.resolve_all(state.analyses, state.evidence, state.as_of, resolver, opts)
        for a in state.analyses:
            if a.resolution is not None:
                await emit(
                    "resolution.applied",
                    "resolver",
                    {"key": a.key, **a.resolution.model_dump(mode="json")},
                    t0,
                )

    @staticmethod
    async def resolve_all(
        analyses: list[SlotAnalysis],
        evidence: EvidenceSet,
        as_of: date,
        resolver: Resolver,
        opts: GraphOptions | None = None,
    ) -> list[SlotAnalysis]:
        by_id = evidence.assertion_by_id()
        docs = evidence.document_by_id()
        out = []
        for a in analyses:
            items = [by_id[i] for i in a.row.assertion_ids if i in by_id]
            if not items:
                out.append(a.model_copy(update={"resolution": None}))
                continue
            if opts is not None and opts.resolve_override is not None and a.conflict is not None:
                res = await opts.resolve_override(a, evidence, as_of)
            else:
                res = resolver.resolve(
                    a.conflict.id if a.conflict else a.key,
                    a.row.slot_key,
                    items,
                    docs,
                    evidence.edges,
                    as_of,
                    a.row.requested_scope,
                )
            out.append(a.model_copy(update={"resolution": res}))
        return out

    def _edges_for(self, evidence: EvidenceSet, new_items: list[Any]) -> list[Any]:
        doc_ids = {it.document.id for it in evidence.items} | {it.document.id for it in new_items}
        return [e for e in self.retriever.corpus.edges if e.from_doc in doc_ids or e.to_doc in doc_ids]

    @staticmethod
    def _evidence_payload(ev: EvidenceSet) -> dict[str, Any]:
        return {
            "items": [
                {
                    "chunk_id": it.chunk.id,
                    "document_id": it.document.id,
                    "title": it.document.title,
                    "version": it.document.version_label,
                    "section": it.chunk.section_path[-1:] or [],
                    "score": it.score,
                    "sources": it.sources,
                    "temporal_status": it.temporal_status.value,
                    "injection": it.chunk.injection_flag,
                    "assertions": len(it.assertions),
                }
                for it in ev.items
            ],
            "diagnostics": ev.diagnostics,
        }

    async def re_resolve(
        self,
        trace_id: str,
        plan: QueryPlan,
        evidence: EvidenceSet,
        analyses: list[SlotAnalysis],
        as_of: date,
        resolver: Resolver,
    ) -> FinalAnswer:
        """Time travel / live policy edit: rerun resolver + deterministic writer + code checks.
        No LLM call, no retrieval: evidence is fixed, so this is pure, fast code."""
        t0 = time.perf_counter()
        plan = plan.model_copy(update={"as_of": as_of})
        refreshed = evidence.model_copy(
            update={
                "items": [
                    it.model_copy(
                        update={
                            "temporal_status": self.retriever.corpus.temporal_status(it.document.id, as_of)
                            if it.document.id in self.retriever.corpus.documents
                            and not it.chunk.injection_flag
                            else it.temporal_status
                        }
                    )
                    for it in evidence.items
                ]
            }
        )
        analyses = self.analyst.analyze(plan, refreshed)
        analyses = await self.resolve_all(analyses, refreshed, as_of, resolver)
        draft = self.synthesizer.template_draft(plan, refreshed, analyses)
        report, _ = await self.critic.review(plan, draft, refreshed, analyses, allow_counter=False)
        usage = Usage(latency_ms=round((time.perf_counter() - t0) * 1000, 2))
        return await self.finalizer.finalize(
            trace_id, plan, refreshed, analyses, draft, report.objections, 0, usage, phrase=False
        )
