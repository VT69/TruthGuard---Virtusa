"""Assertion extraction: chunks -> (subject, attribute, scope, value) tuples with quote spans.

Two extractors share one contract and one validator:
  * RuleExtractor  - lexicon-driven, deterministic, no API key (default / offline / fallback)
  * LLMExtractor   - prompt `assertion_extract`, used in live mode; every LLM assertion is
                     re-validated in code (registry ids, verbatim quote, parseable value)
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from pydantic import BaseModel, Field

from truthguard.core.ids import stable_id
from truthguard.core.logging import get_logger
from truthguard.domain.enums import Modality, Provenance, ValueKind
from truthguard.domain.models import Assertion, Chunk, Document
from truthguard.ingestion.lexicon import AttributeDef, Lexicon, Match
from truthguard.ingestion.normalize import ParsedValue, detect_modality, find_values
from truthguard.llm.gateway import LLMGateway
from truthguard.llm.prompts import evidence_block, load_prompt

log = get_logger(__name__)

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z\"'(₹])|;\s+")


@dataclass(frozen=True)
class Clause:
    text: str
    start: int  # offset within chunk text


def clauses_of(chunk: Chunk) -> list[Clause]:
    """Split a chunk into clauses (lines, then sentences and semicolons). The heading line
    of the section is context, not a clause."""
    heading = chunk.section_path[-1] if chunk.section_path else None
    out: list[Clause] = []
    pos = 0
    for line in chunk.text.split("\n"):
        line_start = pos
        pos += len(line) + 1
        stripped = line.strip()
        if not stripped or (heading is not None and stripped == heading.strip()):
            continue
        body_offset = line_start + (len(line) - len(line.lstrip()))
        if stripped.startswith("- "):
            stripped, body_offset = stripped[2:], body_offset + 2
        cursor = 0
        for part in _SENTENCE_SPLIT.split(stripped):
            idx = stripped.find(part, cursor)
            if idx < 0 or not part.strip():
                continue
            cursor = idx + len(part)
            out.append(Clause(part.rstrip(), body_offset + idx))
    return out


class RuleExtractor:
    def __init__(self, lexicon: Lexicon) -> None:
        self.lex = lexicon

    def extract(self, chunk: Chunk, doc: Document, doc_subjects: list[str] | None = None) -> list[Assertion]:
        context = list(reversed(chunk.section_path))  # nearest heading first
        context_attrs = [m for h in context for m in self.lex.match_attributes(h)]
        out: list[Assertion] = []
        for clause in clauses_of(chunk):
            attrs = self.lex.match_attributes(clause.text)
            from_context = False
            if not attrs and context_attrs:
                attrs, from_context = [context_attrs[0]], True
            claimed: dict[tuple[int, int], tuple[Match, AttributeDef, ParsedValue]] = {}
            for am in attrs:
                adef = self.lex.attributes[am.id]
                values = find_values(clause.text, adef.kind, adef.count_unit, adef.unit)
                if not values:
                    continue
                after = [v for v in values if v.start >= am.end]
                value = (
                    after[0]
                    if (after and not from_context)
                    else min(values, key=lambda v: abs(v.start - am.start) if not from_context else v.start)
                )
                span = (value.start, value.end)
                prev = claimed.get(span)
                if prev is None or (am.strong, am.length) > (prev[0].strong, prev[0].length):
                    claimed[span] = (am, adef, value)
            for _match, adef, value in claimed.values():
                subject = self._subject(clause.text, value, adef, context, doc, doc_subjects or [])
                if subject is None:
                    continue
                scope = self._scope(clause.text, adef, context)
                out.append(self._build(chunk, doc, clause, subject, adef, value, scope, from_context))
        return out

    def _subject(
        self,
        text: str,
        value: ParsedValue,
        adef: AttributeDef,
        context: list[str],
        doc: Document,
        doc_subjects: list[str],
    ) -> str | None:
        def allowed(sid: str) -> bool:
            return not adef.subjects or sid in adef.subjects

        here = [m for m in self.lex.match_subjects(text) if allowed(m.id)]
        if here:
            return min(here, key=lambda m: abs(m.start - value.start)).id
        for heading in context:
            hs = [m for m in self.lex.match_subjects(heading) if allowed(m.id)]
            if hs:
                return hs[0].id
        if adef.default_subject:
            return adef.default_subject
        for sid in doc_subjects:
            if allowed(sid):
                return sid
        title_subjects = [m.id for m in self.lex.match_subjects(doc.title) if allowed(m.id)]
        return title_subjects[0] if title_subjects else None

    def _scope(self, text: str, adef: AttributeDef, context: list[str]) -> str:
        if not adef.scopes:
            return "all"
        found = self.lex.match_scopes(text, adef.scopes)
        if found:
            return found[0]
        if adef.default_scope:
            return adef.default_scope
        for heading in context:
            hs = self.lex.match_scopes(heading, adef.scopes)
            if hs:
                return hs[0]
        return "all"

    def _build(
        self,
        chunk: Chunk,
        doc: Document,
        clause: Clause,
        subject: str,
        adef: AttributeDef,
        value: ParsedValue,
        scope: str,
        from_context: bool,
    ) -> Assertion:
        modality = detect_modality(clause.text)
        if adef.kind == ValueKind.MODALITY:
            modality = Modality(value.value_norm.split(":", 1)[1])
        quote_start = chunk.char_start + clause.start
        return Assertion(
            id=stable_id("as", chunk.id, subject, adef.id, scope, value.value_norm, clause.start),
            chunk_id=chunk.id,
            document_id=doc.id,
            subject_id=subject,
            attribute=adef.id,
            kind=adef.kind,
            value_norm=value.value_norm,
            value_display=value.value_display,
            value_num=value.value_num,
            unit=value.unit,
            scope=scope,
            modality=modality,
            effective_from=doc.effective_from or doc.published_at,
            effective_to=doc.effective_to,
            quote=clause.text,
            quote_start=quote_start,
            quote_end=quote_start + len(clause.text),
            section_path=chunk.section_path,
            provenance=Provenance.INFERRED,
            confidence=0.8 if from_context else 0.9,
            quarantined=chunk.injection_flag,
            inherited_slot=from_context,
        )


# --------------------------------------------------------------------------- LLM extractor


class _LLMAssertion(BaseModel):
    subject_id: str
    attribute: str
    scope: str = "all"
    value_text: str
    modality: str = "states"
    quote: str


class _LLMAssertions(BaseModel):
    assertions: list[_LLMAssertion] = Field(default_factory=list)


class LLMExtractor:
    def __init__(self, lexicon: Lexicon, gateway: LLMGateway, model: str) -> None:
        self.lex = lexicon
        self.gateway = gateway
        self.model = model

    async def extract(self, chunk: Chunk, doc: Document) -> list[Assertion]:
        ents, attrs, scopes = self.lex.registry_prompt()
        prompt = load_prompt("assertion_extract").render(
            entities=ents,
            attributes=attrs,
            scopes=scopes,
            section=" > ".join(chunk.section_path),
            evidence=evidence_block(doc.id, chunk.id, f'title="{doc.title}"', chunk.text),
        )
        result = await self.gateway.structured(prompt, _LLMAssertions, self.model)
        out: list[Assertion] = []
        for item in result.assertions:
            a = self._validate(item, chunk, doc)
            if a is not None:
                out.append(a)
        return out

    def _validate(self, item: _LLMAssertion, chunk: Chunk, doc: Document) -> Assertion | None:
        adef = self.lex.attributes.get(item.attribute)
        if adef is None or item.subject_id not in self.lex.subjects:
            return None
        idx = chunk.text.find(item.quote)
        if idx < 0 or not item.quote.strip():
            log.info("extract.llm_quote_rejected", chunk=chunk.id)
            return None
        values = find_values(item.value_text, adef.kind, adef.count_unit, adef.unit) or find_values(
            item.quote, adef.kind, adef.count_unit, adef.unit
        )
        if not values:
            return None
        scope = item.scope if item.scope in self.lex.scopes or item.scope == "all" else "all"
        clause = Clause(item.quote, idx)
        a = RuleExtractor(self.lex)._build(chunk, doc, clause, item.subject_id, adef, values[0], scope, False)
        return a.model_copy(update={"provenance": Provenance.LLM, "confidence": 0.85})
