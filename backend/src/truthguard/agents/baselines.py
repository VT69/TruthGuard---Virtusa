"""Baselines for the split screen and the eval: the failure TruthGuard fixes.

  B0 naive RAG      dense top-5, one LLM call, answer confidently
  B1 RAG + recency  same retrieval, newest documents first in the prompt
  B2 self-critique  one agent drafts, then critiques its own draft

Live mode calls a real LLM with prompts `baseline_naive` / `baseline_self_critique`.
Offline mode uses transparent deterministic PROXIES of those behaviours (documented in the
eval report): B0 states the value from the highest-ranked chunk, B1 from the newest
document, B2 additionally says "sources differ" when its context shows two values but has
no rules to pick one. Proxies never follow prompt injections, so they do not inflate the
baselines' failure rate.
"""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field

from truthguard.core.logging import get_logger
from truthguard.domain.enums import AnswerStatus
from truthguard.domain.models import (
    Assertion,
    BaselineAnswer,
    Citation,
    Document,
    EvidenceItem,
    QueryPlan,
    ValueFlag,
)
from truthguard.ingestion.lexicon import Lexicon, scopes_overlap
from truthguard.ingestion.normalize import display_for_norm, values_in_text
from truthguard.llm.gateway import LLMGateway
from truthguard.llm.prompts import load_prompt
from truthguard.resolution.engine import Resolver
from truthguard.resolution.policy import PrecedencePolicy
from truthguard.retrieval.corpus import CorpusSnapshot
from truthguard.retrieval.retriever import Retriever
from truthguard.verification.blend_detector import classify_value

log = get_logger(__name__)

BASELINE_TOP_K = 5


class _BaselineOut(BaseModel):
    answer: str
    mentions_conflict: bool = False
    sources: list[str] = Field(default_factory=list)


class BaselineRunner:
    def __init__(
        self, lexicon: Lexicon, corpus: CorpusSnapshot, retriever: Retriever, gateway: LLMGateway, model: str
    ) -> None:
        self.lex = lexicon
        self.corpus = corpus
        self.retriever = retriever
        self.gateway = gateway
        self.model = model

    async def _context(self, plan: QueryPlan) -> list[EvidenceItem]:
        vec = self.retriever.embedder.embed([plan.question])[0]
        hits = await self.retriever.dense.search(vec, BASELINE_TOP_K)
        items = []
        for cid, score in hits:
            c = self.corpus.chunks[cid]
            d = self.corpus.documents[c.document_id]
            items.append(
                EvidenceItem(
                    chunk=c,
                    document=d,
                    score=score,
                    sources=["dense"],
                    assertions=list(self.corpus.by_chunk.get(cid, [])),
                )
            )
        return items

    async def run(
        self, mode: str, trace_id: str, plan: QueryPlan, policy: PrecedencePolicy
    ) -> BaselineAnswer:
        items = await self._context(plan)
        if mode == "B1":
            items.sort(key=lambda it: it.document.sort_date or date.min, reverse=True)
        if self.gateway.live:
            try:
                ans = await self._llm(mode, trace_id, plan, items)
                return self._flag(ans, plan, policy)
            except Exception as exc:
                log.warning("baseline.llm_failed", mode=mode, error=str(exc)[:200])
        return self._flag(self._proxy(mode, trace_id, plan, items), plan, policy)

    # ------------------------------------------------------------------ offline proxies

    def _candidates(
        self, plan: QueryPlan, items: list[EvidenceItem]
    ) -> dict[str, list[tuple[Assertion, Document]]]:
        out: dict[str, list[tuple[Assertion, Document]]] = {}
        for slot in plan.slots:
            found = []
            for it in items:
                if it.chunk.injection_flag:
                    continue
                for a in it.assertions:
                    if a.slot_key == slot.key and scopes_overlap(a.scope, slot.scope):
                        found.append((a, it.document))
            out[f"{slot.key}#{slot.scope or '*'}"] = found
        return out

    def _proxy(self, mode: str, trace_id: str, plan: QueryPlan, items: list[EvidenceItem]) -> BaselineAnswer:
        sentences: list[str] = []
        values: list[str] = []
        cites: list[Citation] = []
        mentions_conflict = False
        for slot in plan.slots:
            cands = self._candidates(plan, items)[f"{slot.key}#{slot.scope or '*'}"]
            label = self.lex.slot_phrase(slot.key, slot.scope)
            if not cands:
                continue
            distinct = list(dict.fromkeys(a.value_norm for a, _ in cands))
            if mode == "B2" and len(distinct) > 1:
                mentions_conflict = True
                parts = []
                for v in distinct:
                    a, d = next((a, d) for a, d in cands if a.value_norm == v)
                    parts.append(f"{d.title} says {a.value_display}")
                    cites.append(Citation(chunk_id=a.chunk_id, document_id=a.document_id, quote=a.quote))
                    values.append(v)
                sentences.append(f"Sources differ on the {label}: " + "; ".join(parts) + ".")
                continue
            a, d = cands[0]
            values.append(a.value_norm)
            cites.append(Citation(chunk_id=a.chunk_id, document_id=a.document_id, quote=a.quote))
            sentences.append(f"The {label} is {a.value_display}.")
        if not sentences:
            return BaselineAnswer(
                trace_id=trace_id,
                question=plan.question,
                as_of=plan.as_of,
                mode=mode,
                status=AnswerStatus.INSUFFICIENT_EVIDENCE,
                answer_text="I could not find this in the documents.",
            )
        status = AnswerStatus.UNRESOLVED_CONFLICT if mentions_conflict else AnswerStatus.VERIFIED
        return BaselineAnswer(
            trace_id=trace_id,
            question=plan.question,
            as_of=plan.as_of,
            mode=mode,
            status=status,
            answer_text=" ".join(sentences),
            stated_values=values,
            citations=cites,
            mentions_conflict=mentions_conflict,
        )

    # ------------------------------------------------------------------ live LLM

    async def _llm(
        self, mode: str, trace_id: str, plan: QueryPlan, items: list[EvidenceItem]
    ) -> BaselineAnswer:
        ctx = "\n\n".join(f"[{it.document.title}]\n{it.chunk.text}" for it in items)
        out = await self.gateway.structured(
            load_prompt("baseline_naive").render(evidence=ctx, question=plan.question),
            _BaselineOut,
            self.model,
        )
        if mode == "B2":
            out = await self.gateway.structured(
                load_prompt("baseline_self_critique").render(
                    evidence=ctx, question=plan.question, draft=out.answer
                ),
                _BaselineOut,
                self.model,
            )
        kinds = {self.lex.attributes[s.attribute].kind for s in plan.slots}
        prefixes = {k.value for k in kinds}
        stated = [
            v
            for v in sorted(values_in_text(out.answer))
            if {
                "INR": "money",
                "PCT": "percent",
                "DAYS": "days",
                "MONTHS": "months",
                "YEARS": "years",
                "COUNT": "count",
            }.get(v.split(":")[0])
            in prefixes
        ]
        idk = not stated and any(
            w in out.answer.lower()
            for w in ("not found", "don't know", "do not know", "no information", "cannot find")
        )
        status = (
            AnswerStatus.INSUFFICIENT_EVIDENCE
            if idk
            else AnswerStatus.UNRESOLVED_CONFLICT
            if out.mentions_conflict
            else AnswerStatus.VERIFIED
        )
        return BaselineAnswer(
            trace_id=trace_id,
            question=plan.question,
            as_of=plan.as_of,
            mode=mode,
            status=status,
            answer_text=out.answer,
            stated_values=stated,
            mentions_conflict=out.mentions_conflict,
        )

    # ------------------------------------------------------------------ overlay

    def _flag(self, ans: BaselineAnswer, plan: QueryPlan, policy: PrecedencePolicy) -> BaselineAnswer:
        """Run the blend detector on the baseline output against the WHOLE corpus slot."""
        resolver = Resolver(policy)
        flags: list[ValueFlag] = []
        for slot in plan.slots:
            items = [a for a in self.corpus.by_slot.get(slot.key, []) if scopes_overlap(a.scope, slot.scope)]
            if not items:
                continue
            res = resolver.resolve(
                "baseline", slot.key, items, self.corpus.documents, self.corpus.edges, plan.as_of, slot.scope
            )
            prefix = items[0].value_norm.split(":")[0]
            for v in ans.stated_values:
                if v.split(":")[0] != prefix:
                    continue
                verdict = classify_value(v, items, res)
                docs = sorted(
                    {
                        self.corpus.documents[a.document_id].title
                        for a in items
                        if a.id in verdict.assertion_ids
                    }
                )
                expl = {
                    "winner": "Matches the source that wins under the precedence rules.",
                    "position": "One of several disagreeing sources; the corpus does not decide between them.",
                    "stale": f"Comes from a source set aside by {verdict.rule.value if verdict.rule else 'a rule'}"
                    f" ({', '.join(docs)}).",
                    "quarantined": "Comes from a quarantined prompt-injection passage.",
                    "blended": "Matches no single source: blended or invented.",
                }[verdict.verdict]
                flags.append(
                    ValueFlag(
                        value_norm=v,
                        value_display=display_for_norm(v),
                        verdict=verdict.verdict,
                        rule=verdict.rule,
                        documents=docs,
                        explanation=expl,
                    )
                )
        return ans.model_copy(update={"value_flags": flags})
