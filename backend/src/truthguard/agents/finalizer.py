"""Finalizer (code): status, conflict cards, calibrated confidence, optional phrasing.

Status precedence: any unresolved slot -> UNRESOLVED_CONFLICT; any decided conflict ->
RESOLVED_CONFLICT; all slots agree -> VERIFIED; nothing answerable -> INSUFFICIENT_EVIDENCE.
Blocking objections that survive the revision loop downgrade the answer: a critical one
makes it abstain (fail closed), a major one stays visible and lowers confidence.
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel

from truthguard.core.logging import get_logger
from truthguard.domain.enums import AnswerStatus, DocStatus, ResolutionStatus, RuleId, Severity
from truthguard.domain.models import (
    AnswerClaim,
    ConfidenceBreakdown,
    ConflictView,
    DraftAnswer,
    EvidenceSet,
    FinalAnswer,
    Objection,
    Position,
    QueryPlan,
    SlotAnalysis,
    Usage,
)
from truthguard.ingestion.lexicon import Lexicon
from truthguard.ingestion.normalize import values_in_text
from truthguard.llm.gateway import LLMGateway
from truthguard.llm.prompts import load_prompt
from truthguard.verification.staleness import stale_reason

log = get_logger(__name__)


@dataclass
class ConfidenceWeights:
    coverage: float = 0.25
    entailment: float = 0.25
    resolution: float = 0.35
    base: float = 0.15
    inferred_metadata_penalty: float = 0.1
    major_penalty: float = 0.15
    critical_penalty: float = 0.4


class _Phrase(BaseModel):
    text: str


class FinalizerAgent:
    def __init__(
        self,
        lexicon: Lexicon,
        gateway: LLMGateway,
        model: str,
        abstain_threshold: float = 0.35,
        weights: ConfidenceWeights | None = None,
    ) -> None:
        self.lex = lexicon
        self.gateway = gateway
        self.model = model
        self.abstain_threshold = abstain_threshold
        self.w = weights or ConfidenceWeights()

    async def finalize(
        self,
        trace_id: str,
        plan: QueryPlan,
        evidence: EvidenceSet,
        analyses: list[SlotAnalysis],
        draft: DraftAnswer,
        residual: list[Objection],
        revisions: int,
        usage: Usage,
        phrase: bool = True,
    ) -> FinalAnswer:
        answered = [a for a in analyses if a.row.assertion_ids and a.resolution is not None]
        statuses = [a.resolution.status for a in answered if a.resolution]
        if not answered:
            status = AnswerStatus.INSUFFICIENT_EVIDENCE
        elif ResolutionStatus.UNRESOLVED in statuses:
            status = AnswerStatus.UNRESOLVED_CONFLICT
        elif any(s in (ResolutionStatus.RESOLVED, ResolutionStatus.SCOPE_SPLIT) for s in statuses):
            status = AnswerStatus.RESOLVED_CONFLICT
        else:
            status = AnswerStatus.VERIFIED

        blocking = [o for o in residual if o.blocking]
        critical = [o for o in blocking if o.severity == Severity.CRITICAL]
        confidence = self._confidence(plan, evidence, analyses, draft, blocking)
        text = draft.summary
        claims = draft.claims
        if critical:
            status = AnswerStatus.INSUFFICIENT_EVIDENCE
            text = (
                "TruthGuard withheld the answer: the critic found problems it could not fix ("
                + "; ".join(o.type.value for o in critical[:3])
                + "). "
                + draft.summary
            )
        elif status != AnswerStatus.INSUFFICIENT_EVIDENCE and confidence.score < self.abstain_threshold:
            status = AnswerStatus.INSUFFICIENT_EVIDENCE
            text = "Confidence is too low to answer reliably. " + draft.summary
        if status == AnswerStatus.INSUFFICIENT_EVIDENCE and not answered:
            text = " ".join(draft.missing) or "The corpus does not contain an answer to this question."
            claims = []
        elif phrase and self.gateway.live and status != AnswerStatus.INSUFFICIENT_EVIDENCE:
            text = await self._phrase(status, text)

        by_assertion = evidence.assertion_by_id()
        injection_docs = sorted(
            {by_assertion[q].document_id for a in analyses for q in a.quarantined_ids if q in by_assertion}
        )
        stale = sorted({o.evidence for o in residual if o.type.value == "STALE_CITATION"})
        blends = sorted(
            {o.evidence for o in residual if o.type.value in ("BLENDED_VALUE", "IGNORED_RESOLUTION")}
        )
        return FinalAnswer(
            trace_id=trace_id,
            question=plan.question,
            as_of=plan.as_of,
            status=status,
            answer_text=text,
            claims=claims,
            conflicts=self.conflict_views(plan, evidence, analyses),
            confidence=confidence,
            residual_objections=residual,
            missing_evidence=draft.missing,
            injection_flags=injection_docs,
            stale_citations=stale,
            blend_flags=blends,
            revisions=revisions,
            usage=usage,
        )

    def conflict_views(
        self, plan: QueryPlan, evidence: EvidenceSet, analyses: list[SlotAnalysis]
    ) -> list[ConflictView]:
        by_id = evidence.assertion_by_id()
        docs = evidence.document_by_id()
        chunks = evidence.chunk_by_id()
        out: list[ConflictView] = []
        for an in analyses:
            if an.conflict is None or an.resolution is None:
                continue
            res = an.resolution
            winners = set(res.winner_assertion_ids)
            winner_values = {by_id[w].value_norm for w in winners if w in by_id}
            positions: list[Position] = []
            for aid in an.conflict.assertion_ids:
                a = by_id.get(aid)
                if a is None:
                    continue
                d = docs[a.document_id]
                rule = res.eliminated_by.get(aid)
                if res.status == ResolutionStatus.UNRESOLVED and rule is None:
                    outcome = "unresolved"
                elif aid in winners:
                    outcome = "scope_winner" if res.status == ResolutionStatus.SCOPE_SPLIT else "winner"
                elif rule is not None:
                    outcome = "eliminated"
                elif a.value_norm in winner_values:
                    outcome = "agrees"
                else:
                    outcome = "unresolved"
                chunk = chunks.get(a.chunk_id)
                tstatus = DocStatus.ACTIVE
                if chunk is not None and stale_reason(chunk, d, docs, evidence.edges, plan.as_of):
                    tstatus = (
                        DocStatus.SUPERSEDED
                        if rule == RuleId.R1
                        else (DocStatus.NOT_YET_EFFECTIVE if rule == RuleId.R0 else DocStatus.SUPERSEDED)
                    )
                elif rule == RuleId.R0:
                    tstatus = DocStatus.NOT_YET_EFFECTIVE
                positions.append(
                    Position(
                        assertion_id=aid,
                        document_id=d.id,
                        document_title=d.title,
                        version_label=d.version_label,
                        effective_from=d.effective_from,
                        published_at=d.published_at,
                        authority_tier=d.authority_tier,
                        doc_type=d.doc_type,
                        quote=a.quote,
                        chunk_id=a.chunk_id,
                        value_display=a.value_display,
                        value_norm=a.value_norm,
                        scope=a.scope,
                        outcome=outcome,
                        eliminated_by=rule,
                        temporal_status=tstatus,
                        metadata_inferred=any(
                            m.provenance.value != "explicit"
                            for k, m in d.field_provenance.items()
                            if k in ("effective_from", "published")
                        )
                        or not (d.effective_from or d.published_at),
                    )
                )
            positions.sort(
                key=lambda p: (
                    {"winner": 0, "scope_winner": 0, "unresolved": 1, "agrees": 2, "eliminated": 3}[
                        p.outcome
                    ],
                    p.authority_tier,
                    p.document_id,
                )
            )
            owner = None
            if res.status == ResolutionStatus.UNRESOLVED:
                owner = self.lex.topic_owners.get(self.lex.topic_of(an.row.subject_id) or "general")
            out.append(
                ConflictView(
                    conflict_id=an.conflict.id,
                    slot_key=an.row.slot_key,
                    label=self.lex.slot_label(an.row.slot_key, an.row.requested_scope),
                    conflict_type=an.conflict.conflict_type,
                    severity=an.conflict.severity,
                    positions=positions,
                    resolution=res,
                    who_should_decide=owner,
                )
            )
        return out

    def _confidence(
        self,
        plan: QueryPlan,
        evidence: EvidenceSet,
        analyses: list[SlotAnalysis],
        draft: DraftAnswer,
        blocking: list[Objection],
    ) -> ConfidenceBreakdown:
        w = self.w
        n = max(1, len(plan.slots))
        coverage = sum(1 for a in analyses if a.row.assertion_ids) / n if plan.slots else 0.0
        cited = [c for c in draft.claims if c.citations]
        weakest = min((c.entailment if c.entailment is not None else 1.0 for c in cited), default=0.0)
        strengths = []
        for a in analyses:
            if a.resolution is None:
                continue
            if a.resolution.status == ResolutionStatus.UNRESOLVED:
                strengths.append(0.6)  # confident that it is a genuine, disclosed disagreement
            else:
                strengths.append(a.resolution.strength)
        strength = min(strengths, default=0.0)
        docs = evidence.document_by_id()
        by_id = evidence.assertion_by_id()
        inferred = False
        for a in analyses:
            for wid in a.resolution.winner_assertion_ids if a.resolution else []:
                d = docs.get(by_id[wid].document_id) if wid in by_id else None
                if d and not any(
                    m.provenance.value == "explicit"
                    for k, m in d.field_provenance.items()
                    if k in ("effective_from", "published")
                ):
                    inferred = True
        meta_pen = w.inferred_metadata_penalty if inferred else 0.0
        obj_pen = sum(
            w.critical_penalty if o.severity == Severity.CRITICAL else w.major_penalty for o in blocking
        )
        raw = (
            w.coverage * coverage
            + w.entailment * weakest
            + w.resolution * strength
            + w.base
            - meta_pen
            - obj_pen
        )
        if not plan.slots or coverage == 0:
            raw = 0.0
        return ConfidenceBreakdown(
            retrieval_coverage=round(coverage, 3),
            weakest_entailment=round(weakest, 3),
            resolution_strength=round(strength, 3),
            metadata_penalty=meta_pen,
            objection_penalty=round(obj_pen, 3),
            score=round(max(0.0, min(1.0, raw)), 3),
        )

    async def _phrase(self, status: AnswerStatus, draft_text: str) -> str:
        try:
            prompt = load_prompt("finalizer_phrase").render(status=status.value, draft=draft_text)
            out = await self.gateway.structured(prompt, _Phrase, self.model)
        except Exception as exc:
            log.warning("finalizer.phrase_failed", error=str(exc)[:200])
            return draft_text
        # phrasing may not add, drop or change any value
        if values_in_text(out.text) != values_in_text(draft_text):
            return draft_text
        return out.text


def claims_digest(claims: list[AnswerClaim]) -> list[str]:
    return [f"{c.verdict}:{c.value_norm}" for c in claims]
