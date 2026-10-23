"""Synthesizer: drafts atomic, cited claims that follow the resolver's verdicts.

Live mode uses the strongest configured model (prompt `synthesizer`) with resolutions and
critic objections in context; offline mode, re-resolve (time travel) and any LLM failure use
the deterministic template writer below, which can only state values that exist in the
evidence and always follows the resolutions.
"""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field

from truthguard.core.ids import stable_id
from truthguard.core.logging import get_logger
from truthguard.domain.enums import ResolutionStatus, RuleId
from truthguard.domain.models import (
    AnswerClaim,
    Assertion,
    Citation,
    Document,
    DraftAnswer,
    EvidenceSet,
    Objection,
    QueryPlan,
    SlotAnalysis,
)
from truthguard.ingestion.lexicon import Lexicon
from truthguard.ingestion.normalize import find_values
from truthguard.llm.gateway import LLMGateway
from truthguard.llm.prompts import evidence_block, load_prompt

log = get_logger(__name__)

RULE_REASON = {
    RuleId.R0: "it is not in force on the as-of date",
    RuleId.R1: "it has been superseded",
    RuleId.R2: "it is a lower-authority source",
    RuleId.R3: "it is the general rule and a more specific rule applies",
    RuleId.R4: "a newer version in the same series replaces it",
    RuleId.QUARANTINE: "the passage was quarantined as a prompt injection",
}


def eliminated_rules(an: SlotAnalysis) -> set[RuleId]:
    return set(an.resolution.eliminated_by.values()) - {RuleId.QUARANTINE} if an.resolution else set()


def doc_label(d: Document) -> str:
    bits = [d.title]
    if d.version_label and d.version_label not in d.title:
        bits.append(d.version_label)
    when = d.effective_from or d.published_at
    return f"{' '.join(bits)} ({when.isoformat() if when else 'undated'})"


class _LLMCitation(BaseModel):
    chunk_id: str
    quote: str


class _LLMClaim(BaseModel):
    text: str
    slot_key: str | None = None
    scope: str | None = None
    value_text: str | None = None
    verdict: str = "winner"
    citations: list[_LLMCitation] = Field(default_factory=list)


class _LLMDraft(BaseModel):
    summary: str
    claims: list[_LLMClaim] = Field(default_factory=list)
    disclosed_conflicts: list[str] = Field(default_factory=list)


class SynthesizerAgent:
    def __init__(self, lexicon: Lexicon, gateway: LLMGateway, model: str) -> None:
        self.lex = lexicon
        self.gateway = gateway
        self.model = model

    async def draft(
        self,
        plan: QueryPlan,
        evidence: EvidenceSet,
        analyses: list[SlotAnalysis],
        objections: list[Objection],
        revision: int,
    ) -> DraftAnswer:
        if self.gateway.live and any(a.row.assertion_ids for a in analyses):
            try:
                return await self._llm_draft(plan, evidence, analyses, objections, revision)
            except Exception as exc:  # fail closed on the LLM path: fall back to the deterministic writer
                log.warning("synth.llm_failed", error=str(exc)[:200])
        return self.template_draft(plan, evidence, analyses, revision)

    # ------------------------------------------------------------------ deterministic writer

    def template_draft(
        self, plan: QueryPlan, evidence: EvidenceSet, analyses: list[SlotAnalysis], revision: int = 0
    ) -> DraftAnswer:
        by_id = evidence.assertion_by_id()
        docs = evidence.document_by_id()
        claims: list[AnswerClaim] = []
        sentences: list[str] = []
        missing: list[str] = []
        disclosed: list[str] = []
        for an in analyses:
            label = self._label(an)
            items = [by_id[i] for i in an.row.assertion_ids if i in by_id]
            res = an.resolution
            if not items or res is None:
                missing.append(f"No source in the corpus states the {label}.")
                continue
            if an.conflict is not None:
                disclosed.append(an.conflict.id)
            if res.status == ResolutionStatus.UNRESOLVED:
                sentences.append(self._unresolved(an, items, docs, claims, label, plan.as_of))
                continue
            winners = [by_id[w] for w in res.winner_assertion_ids if w in by_id]
            if res.status == ResolutionStatus.SCOPE_SPLIT or len({w.scope for w in winners}) > 1:
                parts = []
                for w in sorted(winners, key=lambda a: (a.scope != "all", a.scope)):
                    scope_txt = (
                        "in general" if w.scope == "all" else f"for {self.lex.scope_label(w.scope).lower()}"
                    )
                    text = f"The {label} {scope_txt} is {w.value_display}."
                    claims.append(self._claim(an, w, text, "winner", items, res.eliminated_by))
                    parts.append(f"{w.value_display} {scope_txt}")
                sentences.append(f"The {label} is {' and '.join(parts)}.")
            else:
                w = winners[0]
                text = f"The {label} is {w.value_display}."
                claims.append(self._claim(an, w, text, "winner", items, res.eliminated_by))
                sentences.append(text)
            if an.conflict is not None:
                notes = self._superseded_claims(an, items, winners, docs, claims, res.eliminated_by)
                if notes:
                    rules = sorted({r.value for r in eliminated_rules(an)})
                    n = len(notes)
                    sentences.append(
                        f"{n} conflicting source{'s' if n > 1 else ''} "
                        f"{'were' if n > 1 else 'was'} set aside by rule {', '.join(rules)}."
                    )
        if plan.unknown_entities and not plan.slots:
            missing.append("The corpus has no information about " + ", ".join(plan.unknown_entities) + ".")
        elif not plan.slots:
            missing.append("The question does not match any product and attribute covered by the corpus.")
        summary = (
            " ".join(sentences) if sentences else "The corpus does not contain an answer to this question."
        )
        return DraftAnswer(
            summary=summary,
            claims=claims,
            disclosed_conflict_ids=disclosed,
            missing=missing,
            revision=revision,
        )

    def _label(self, an: SlotAnalysis) -> str:
        return self.lex.slot_phrase(an.row.slot_key, an.row.requested_scope)

    def _claim(
        self,
        an: SlotAnalysis,
        a: Assertion,
        text: str,
        verdict: str,
        items: list[Assertion],
        eliminated: dict[str, RuleId],
    ) -> AnswerClaim:
        cites = [Citation(chunk_id=a.chunk_id, document_id=a.document_id, quote=a.quote)]
        if verdict == "winner":
            corroborating = [
                x
                for x in items
                if x.value_norm == a.value_norm
                and x.scope == a.scope
                and x.document_id != a.document_id
                and x.id not in eliminated
            ]
            for x in corroborating[:1]:
                cites.append(Citation(chunk_id=x.chunk_id, document_id=x.document_id, quote=x.quote))
        return AnswerClaim(
            id=stable_id("cl", an.key, a.id, verdict),
            text=text,
            slot_key=an.row.slot_key,
            scope=a.scope,
            value_norm=a.value_norm,
            value_display=a.value_display,
            verdict=verdict,
            citations=cites,
        )

    def _superseded_claims(
        self,
        an: SlotAnalysis,
        items: list[Assertion],
        winners: list[Assertion],
        docs: dict[str, Document],
        claims: list[AnswerClaim],
        eliminated: dict[str, RuleId],
    ) -> list[str]:
        winner_values = {w.value_norm for w in winners}
        notes: list[str] = []
        seen: set[tuple[str, str]] = set()
        for a in sorted(items, key=lambda x: (x.document_id, x.id)):
            rule = eliminated.get(a.id)
            if rule is None or a.value_norm in winner_values or (a.document_id, a.value_norm) in seen:
                continue
            seen.add((a.document_id, a.value_norm))
            d = docs.get(a.document_id)
            name = doc_label(d) if d else a.document_id
            note = f"{name} states {a.value_display}, but {RULE_REASON[rule]} ({rule.value})"
            claims.append(
                self._claim(
                    an,
                    a,
                    f"{name} states {a.value_display}; it does not apply because "
                    f"{RULE_REASON[rule]} ({rule.value}).",
                    "superseded",
                    items,
                    eliminated,
                )
            )
            notes.append(note)
        return notes

    def _unresolved(
        self,
        an: SlotAnalysis,
        items: list[Assertion],
        docs: dict[str, Document],
        claims: list[AnswerClaim],
        label: str,
        as_of: date,
    ) -> str:
        res = an.resolution
        assert res is not None
        alive = [a for a in items if a.id not in res.eliminated_by]
        by_value: dict[str, Assertion] = {}
        for a in sorted(
            alive, key=lambda x: (docs[x.document_id].authority_tier if x.document_id in docs else 9, x.id)
        ):
            by_value.setdefault(a.value_norm, a)
        positions = []
        for a in by_value.values():
            d = docs.get(a.document_id)
            name = doc_label(d) if d else a.document_id
            tier = f"tier {d.authority_tier} {d.doc_type.replace('_', ' ')}" if d else "unknown tier"
            text = f"{name}, a {tier}, states that the {label} is {a.value_display}."
            claims.append(self._claim(an, a, text, "position", items, res.eliminated_by))
            positions.append(f"{name} says {a.value_display}")
        owner = self.lex.topic_owners.get(
            self.lex.topic_of(an.row.subject_id) or "general", "the policy owner"
        )
        flag = (
            " The newer source may be an unofficial update."
            if "possible_unofficial_update" in res.flags
            else ""
        )
        return (
            f"The sources disagree on the {label}: {'; '.join(positions)}.{flag} TruthGuard cannot determine "
            f"which applies as of {as_of.isoformat()}; this should be settled by the {owner}."
        )

    # ------------------------------------------------------------------ LLM writer

    async def _llm_draft(
        self,
        plan: QueryPlan,
        evidence: EvidenceSet,
        analyses: list[SlotAnalysis],
        objections: list[Objection],
        revision: int,
    ) -> DraftAnswer:
        by_id = evidence.assertion_by_id()
        res_lines = []
        for an in analyses:
            res = an.resolution
            if res is None:
                res_lines.append(f"- {an.key}: NO EVIDENCE")
                continue
            win = [by_id[w] for w in res.winner_assertion_ids if w in by_id]
            res_lines.append(
                f"- {an.key}: status={res.status.value}; winners="
                + ", ".join(f"{w.value_display} [{w.scope}] from {w.document_id}/{w.chunk_id}" for w in win)
                + f"; eliminated={ {k: v.value for k, v in res.eliminated_by.items()} }; rationale={res.rationale}"
            )
        blocks = []
        for it in evidence.items:
            if it.chunk.injection_flag:
                continue
            d = it.document
            header = (
                f'title="{d.title}" version="{d.version_label}" effective="{d.effective_from}" '
                f'tier="{d.authority_tier}" status="{it.temporal_status.value}"'
            )
            blocks.append(evidence_block(d.id, it.chunk.id, header, it.chunk.text))
        prompt = load_prompt("synthesizer").render(
            question=plan.question,
            as_of=plan.as_of.isoformat(),
            resolutions="\n".join(res_lines),
            objections="\n".join(
                f"- [{o.severity.value}] {o.type.value}: {o.evidence} -> {o.suggested_fix}"
                for o in objections
            )
            or "(none)",
            evidence="\n\n".join(blocks),
        )
        out = await self.gateway.structured(prompt, _LLMDraft, self.model, temperature=0.2)
        chunks = evidence.chunk_by_id()
        claims: list[AnswerClaim] = []
        for i, c in enumerate(out.claims):
            attr = (c.slot_key or "").partition("|")[2]
            adef = self.lex.attributes.get(attr)
            value_norm = None
            if adef and c.value_text:
                vals = find_values(c.value_text, adef.kind, adef.count_unit, adef.unit)
                value_norm = vals[0].value_norm if vals else None
            cites = [
                Citation(
                    chunk_id=x.chunk_id,
                    document_id=chunks[x.chunk_id].document_id if x.chunk_id in chunks else "?",
                    quote=x.quote,
                )
                for x in c.citations
            ]
            verdict = (
                c.verdict
                if c.verdict in ("winner", "position", "superseded", "info", "corroborated")
                else "info"
            )
            claims.append(
                AnswerClaim(
                    id=stable_id("cl", revision, i, c.text),
                    text=c.text,
                    slot_key=c.slot_key,
                    scope=c.scope,
                    value_norm=value_norm,
                    value_display=c.value_text,
                    verdict=verdict,
                    citations=cites,
                )
            )
        disclosed = [
            an.conflict.id for an in analyses if an.conflict and an.row.slot_key in out.disclosed_conflicts
        ]
        missing = [
            f"No source in the corpus states the {self._label(an)}."
            for an in analyses
            if not an.row.assertion_ids
        ]
        return DraftAnswer(
            summary=out.summary,
            claims=claims,
            disclosed_conflict_ids=disclosed,
            missing=missing,
            revision=revision,
        )
