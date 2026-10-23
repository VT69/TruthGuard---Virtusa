"""Critic: an independent adversary that tries to break the draft.

Independent context (non-negotiable #4): it receives the question, the plan, the draft's
claims and citations, and its own access to the corpus (lineage lookups, counter-retrieval)
- never the synthesizer's reasoning or prompt. In live mode the entailment judge runs on a
different model family from the synthesizer.

Seven checks: span integrity, entailment, staleness, blend detection, counter-retrieval,
coverage of conflicts, unsupported claims.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from truthguard.core.ids import stable_id
from truthguard.core.logging import get_logger
from truthguard.domain.enums import ObjectionType, ResolutionStatus, Severity
from truthguard.domain.models import (
    Citation,
    CriticCheck,
    CriticReport,
    DraftAnswer,
    EvidenceItem,
    EvidenceSet,
    Objection,
    QueryPlan,
    SlotAnalysis,
)
from truthguard.ingestion.lexicon import Lexicon, scopes_overlap
from truthguard.ingestion.normalize import values_in_text
from truthguard.llm.gateway import LLMGateway
from truthguard.llm.prompts import load_prompt
from truthguard.retrieval.corpus import CorpusSnapshot
from truthguard.retrieval.retriever import Retriever
from truthguard.verification.blend_detector import classify_value
from truthguard.verification.nli import NLIScorer
from truthguard.verification.span_check import locate_quote
from truthguard.verification.staleness import stale_reason

log = get_logger(__name__)

ENTAIL_THRESHOLD = 0.6
GRAY_ZONE = (0.45, 0.75)


class _Judgement(BaseModel):
    index: int
    label: str  # ENTAILS | CONTRADICTS | NEUTRAL


class _Judgements(BaseModel):
    judgements: list[_Judgement] = Field(default_factory=list)


def counter_queries(lex: Lexicon, slot_key: str, scope: str | None) -> list[str]:
    subject, _, attr = slot_key.partition("|")
    s, a = lex.subject_label(subject), lex.attribute_label(attr)
    scope_txt = f" {lex.scope_label(scope)}" if scope else ""
    return [
        f"newer version of the {s} {a}{scope_txt}",
        f"{a} amended revised substituted {s}",
        f"exceptions to the {a} for {s}{scope_txt}",
        f"{s} {a} no longer applies update memo email",
    ]


class CriticAgent:
    def __init__(
        self,
        lexicon: Lexicon,
        corpus: CorpusSnapshot,
        retriever: Retriever,
        nli: NLIScorer,
        gateway: LLMGateway,
        judge_model: str,
        counter_retrieval: bool = True,
    ) -> None:
        self.lex = lexicon
        self.corpus = corpus
        self.retriever = retriever
        self.nli = nli
        self.gateway = gateway
        self.judge_model = judge_model
        self.counter_retrieval = counter_retrieval

    async def review(
        self,
        plan: QueryPlan,
        draft: DraftAnswer,
        evidence: EvidenceSet,
        analyses: list[SlotAnalysis],
        *,
        allow_counter: bool = True,
    ) -> tuple[CriticReport, list[EvidenceItem]]:
        objections: list[Objection] = []
        checks: list[CriticCheck] = []
        by_assertion = evidence.assertion_by_id()

        def obj(
            t: ObjectionType,
            sev: Severity,
            evidence_txt: str,
            fix: str,
            claim_id: str | None = None,
            slot: str | None = None,
        ) -> None:
            objections.append(
                Objection(
                    id=stable_id("ob", t.value, claim_id, slot, evidence_txt),
                    type=t,
                    severity=sev,
                    claim_id=claim_id,
                    slot_key=slot,
                    evidence=evidence_txt,
                    suggested_fix=fix,
                )
            )

        # 1. span integrity -------------------------------------------------------------
        bad_spans = 0
        verified_claims = []
        for claim in draft.claims:
            new_cites: list[Citation] = []
            for c in claim.citations:
                chunk = self.corpus.chunks.get(c.chunk_id)
                loc = locate_quote(c.quote, chunk.text) if chunk else None
                if chunk is None or loc is None:
                    bad_spans += 1
                    obj(
                        ObjectionType.FABRICATED_QUOTE,
                        Severity.CRITICAL,
                        f'Quote not found verbatim in chunk {c.chunk_id}: "{c.quote[:120]}"',
                        "Cite a passage that exists word for word in the evidence.",
                        claim.id,
                        claim.slot_key,
                    )
                    new_cites.append(c.model_copy(update={"verified": False}))
                else:
                    new_cites.append(
                        c.model_copy(
                            update={
                                "verified": True,
                                "char_start": chunk.char_start + loc[0],
                                "char_end": chunk.char_start + loc[1],
                            }
                        )
                    )
                if chunk is not None and chunk.injection_flag:
                    obj(
                        ObjectionType.UNSUPPORTED_CLAIM,
                        Severity.CRITICAL,
                        f"Claim cites quarantined (prompt-injection) chunk {c.chunk_id}.",
                        "Remove the claim; quarantined text is never evidence.",
                        claim.id,
                        claim.slot_key,
                    )
            verified_claims.append(claim.model_copy(update={"citations": new_cites}))
        draft.claims = verified_claims
        n_cites = sum(len(c.citations) for c in draft.claims)
        checks.append(
            CriticCheck(
                name="span_integrity",
                passed=bad_spans == 0,
                detail=f"{n_cites - bad_spans}/{n_cites} quotes found verbatim",
            )
        )

        # 2. entailment -----------------------------------------------------------------
        weak = 0
        gray: list[tuple[int, str, str]] = []
        scored = []
        for i, claim in enumerate(draft.claims):
            if not claim.citations:
                scored.append(claim)
                continue
            best_e, best_c = 0.0, 0.0
            for c in claim.citations:
                chunk = self.corpus.chunks.get(c.chunk_id)
                context = " > ".join(chunk.section_path) if chunk else ""
                s = self.nli.score(claim.text, c.quote, context)
                if s.entailment > best_e:
                    best_e, best_c = s.entailment, s.contradiction
            if GRAY_ZONE[0] <= best_e <= GRAY_ZONE[1]:
                gray.append((i, claim.text, claim.citations[0].quote))
            scored.append(claim.model_copy(update={"entailment": best_e, "contradiction": best_c}))
        draft.claims = scored
        if gray and self.gateway.live:
            await self._judge(draft, gray)
        for claim in draft.claims:
            if claim.citations and (claim.entailment or 0) < ENTAIL_THRESHOLD:
                weak += 1
                obj(
                    ObjectionType.NOT_ENTAILED,
                    Severity.MAJOR,
                    f"Cited span does not entail the claim (entailment {claim.entailment:.2f}).",
                    "State only what the cited span says, or cite a span that says it.",
                    claim.id,
                    claim.slot_key,
                )
        checks.append(
            CriticCheck(
                name="entailment",
                passed=weak == 0,
                detail=f"{len(draft.claims) - weak}/{len(draft.claims)} claims entailed",
            )
        )

        # 3. staleness ------------------------------------------------------------------
        stale = 0
        for claim in draft.claims:
            if claim.verdict not in ("winner", "corroborated"):
                continue
            for c in claim.citations:
                chunk = self.corpus.chunks.get(c.chunk_id)
                doc = self.corpus.documents.get(c.document_id)
                if chunk is None or doc is None:
                    continue
                reason = stale_reason(chunk, doc, self.corpus.documents, self.corpus.edges, plan.as_of)
                if reason:
                    stale += 1
                    obj(
                        ObjectionType.STALE_CITATION,
                        Severity.MAJOR,
                        f"Cited as current but {reason}.",
                        "Cite the version in force on the as-of date.",
                        claim.id,
                        claim.slot_key,
                    )
        checks.append(CriticCheck(name="staleness", passed=stale == 0, detail=f"{stale} stale citation(s)"))

        # 4. blend detection ------------------------------------------------------------
        blends = 0
        for claim in draft.claims:
            an = next((a for a in analyses if a.row.slot_key == claim.slot_key), None)
            if an is None or claim.verdict not in ("winner", "corroborated"):
                continue
            items = [by_assertion[i] for i in an.row.assertion_ids if i in by_assertion]
            prefixes = {a.value_norm.split(":")[0] for a in items}
            stated = (
                {claim.value_norm}
                if claim.value_norm
                else {
                    v
                    for v in values_in_text(claim.text)
                    if v.split(":")[0] in prefixes  # same kind as the slot
                }
            )
            for v in stated:
                verdict = classify_value(v, items, an.resolution)
                if verdict.verdict == "blended":
                    blends += 1
                    obj(
                        ObjectionType.BLENDED_VALUE,
                        Severity.CRITICAL,
                        f"Value {v} matches no single source for {claim.slot_key}.",
                        "Use a value exactly as stated by one source.",
                        claim.id,
                        claim.slot_key,
                    )
                elif verdict.verdict in ("stale", "quarantined"):
                    blends += 1
                    obj(
                        ObjectionType.IGNORED_RESOLUTION,
                        Severity.CRITICAL,
                        f"Value {v} comes only from a source eliminated by {verdict.rule.value if verdict.rule else '?'}.",
                        "State the winning value from the resolution.",
                        claim.id,
                        claim.slot_key,
                    )
        checks.append(
            CriticCheck(name="blend_detection", passed=blends == 0, detail=f"{blends} blended/stale value(s)")
        )

        # 5. counter-retrieval ----------------------------------------------------------
        new_items: list[EvidenceItem] = []
        queries: list[str] = []
        if self.counter_retrieval and allow_counter:
            known_chunks = {it.chunk.id for it in evidence.items}
            known_assertions = set(by_assertion)
            for an in analyses:
                qs = counter_queries(self.lex, an.row.slot_key, an.row.requested_scope)
                queries += qs
                hits = await self.retriever.search(qs, top_k=8)
                for cid, score, _src in hits:
                    if cid in known_chunks:
                        continue
                    fresh = [
                        a
                        for a in self.corpus.by_chunk.get(cid, [])
                        if a.slot_key == an.row.slot_key
                        and a.id not in known_assertions
                        and not a.quarantined
                        and scopes_overlap(a.scope, an.row.requested_scope)
                    ]
                    if not fresh:
                        continue
                    chunk = self.corpus.chunks[cid]
                    doc = self.corpus.documents[chunk.document_id]
                    item = EvidenceItem(
                        chunk=chunk,
                        document=doc,
                        score=score,
                        sources=["counter"],
                        temporal_status=self.corpus.temporal_status(doc.id, plan.as_of),
                        assertions=list(self.corpus.by_chunk.get(cid, [])),
                    )
                    new_items.append(item)
                    known_chunks.add(cid)
                    current_values = {
                        by_assertion[i].value_norm for i in an.row.assertion_ids if i in by_assertion
                    }
                    for a in fresh:
                        sev = (
                            Severity.MAJOR
                            if a.value_norm not in current_values or not current_values
                            else Severity.MINOR
                        )
                        obj(
                            ObjectionType.NEW_CONFLICT,
                            sev,
                            f"Counter-search found {doc.label()} stating {a.value_display} for {an.row.slot_key}, "
                            "which the analysis did not consider.",
                            "Re-run analysis and resolution with this source included.",
                            None,
                            an.row.slot_key,
                        )
        checks.append(
            CriticCheck(
                name="counter_retrieval",
                passed=not new_items,
                detail=f"{len(queries)} adversarial queries, {len(new_items)} new source(s)"
                if allow_counter and self.counter_retrieval
                else "skipped",
            )
        )

        # 6. coverage of conflicts ------------------------------------------------------
        undisclosed = 0
        for an in analyses:
            if an.conflict is None:
                continue
            claims_for = [c for c in draft.claims if c.slot_key == an.row.slot_key]
            disclosed = an.conflict.id in draft.disclosed_conflict_ids
            unresolved = an.resolution is not None and an.resolution.status == ResolutionStatus.UNRESOLVED
            if unresolved:
                positions = {c.value_norm for c in claims_for if c.verdict == "position"}
                if len(positions) < 2:
                    disclosed = False
            if not disclosed:
                undisclosed += 1
                obj(
                    ObjectionType.UNDISCLOSED_CONFLICT,
                    Severity.CRITICAL if unresolved else Severity.MAJOR,
                    f"Conflict on {an.row.slot_key} ({', '.join(an.conflict.distinct_values)}) is not disclosed.",
                    "Disclose the conflict with every position and its source.",
                    None,
                    an.row.slot_key,
                )
        checks.append(
            CriticCheck(
                name="coverage", passed=undisclosed == 0, detail=f"{undisclosed} undisclosed conflict(s)"
            )
        )

        # 7. unsupported claims ---------------------------------------------------------
        unsupported = 0
        for claim in draft.claims:
            if claim.verdict != "info" and not claim.citations:
                unsupported += 1
                obj(
                    ObjectionType.UNSUPPORTED_CLAIM,
                    Severity.MAJOR,
                    f'Claim has no citation: "{claim.text[:100]}"',
                    "Cite evidence or drop the claim.",
                    claim.id,
                    claim.slot_key,
                )
        checks.append(
            CriticCheck(
                name="unsupported_claims", passed=unsupported == 0, detail=f"{unsupported} uncited claim(s)"
            )
        )

        report = CriticReport(
            objections=objections,
            checks=checks,
            counter_queries=queries,
            new_assertion_ids=[a.id for it in new_items for a in it.assertions],
        )
        return report, new_items

    async def _judge(self, draft: DraftAnswer, gray: list[tuple[int, str, str]]) -> None:
        pairs = "\n".join(f"[{i}] CLAIM: {claim}\n    SPAN: {span}" for i, claim, span in gray)
        try:
            prompt = load_prompt("critic_judge").render(pairs=pairs)
            out = await self.gateway.structured(prompt, _Judgements, self.judge_model)
        except Exception as exc:
            log.warning("critic.judge_failed", error=str(exc)[:200])
            return
        for j in out.judgements:
            if 0 <= j.index < len(draft.claims):
                c = draft.claims[j.index]
                e = {"ENTAILS": 0.95, "NEUTRAL": 0.4, "CONTRADICTS": 0.05}.get(j.label.upper(), c.entailment)
                draft.claims[j.index] = c.model_copy(update={"entailment": e})
