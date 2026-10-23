"""Query planner: question -> QueryPlan (intent, slots, as-of date, sub-queries). Never answers.

Live mode asks a small fast model (prompt `planner`) and validates every id against the
registry; offline mode (and any LLM failure) uses the lexicon. Unknown products ("Titanium
card", "home loan") are reported instead of being mapped onto a near neighbour, which is
what lets the system abstain correctly.
"""

from __future__ import annotations

import re
from datetime import date

from pydantic import BaseModel, Field

from truthguard.core.logging import get_logger
from truthguard.domain.models import QueryPlan, SlotRef
from truthguard.ingestion.lexicon import Lexicon
from truthguard.ingestion.metadata import MONTHS, parse_date
from truthguard.ingestion.normalize import values_in_text
from truthguard.llm.gateway import LLMGateway
from truthguard.llm.prompts import load_prompt

log = get_logger(__name__)

OVERVIEW_CUE = re.compile(r"\b(?:overview|tell me about|summari[sz]e|all (?:the )?(?:fees|charges))\b", re.I)
DATE_PATTERNS = [
    re.compile(rf"\b(\d{{1,2}} (?:{MONTHS}) \d{{4}})\b", re.I),
    re.compile(r"\b(\d{4}-\d{2}-\d{2})\b"),
    re.compile(rf"\b((?:{MONTHS}) \d{{4}})\b", re.I),
]


def parse_question_date(question: str) -> date | None:
    for pat in DATE_PATTERNS:
        m = pat.search(question)
        if not m:
            continue
        raw = m.group(1)
        d = parse_date(raw)
        if d is None:
            d = parse_date(f"1 {raw}")
        if d is not None:
            return d
    return None


class _LLMSlot(BaseModel):
    subject_id: str
    attribute: str
    scope: str | None = None


class _LLMPlan(BaseModel):
    intent: str = "lookup"
    slots: list[_LLMSlot] = Field(default_factory=list)
    unknown_entities: list[str] = Field(default_factory=list)
    as_of: str | None = None
    claimed_values: list[str] = Field(default_factory=list)
    sub_queries: list[str] = Field(default_factory=list)


class PlannerAgent:
    def __init__(self, lexicon: Lexicon, gateway: LLMGateway, model: str) -> None:
        self.lex = lexicon
        self.gateway = gateway
        self.model = model

    async def plan(self, question: str, default_as_of: date, explicit_as_of: bool = False) -> QueryPlan:
        rule_plan = self.rule_plan(question, default_as_of, explicit_as_of)
        if not self.gateway.live:
            return rule_plan
        try:
            return await self._llm_plan(question, default_as_of, explicit_as_of, rule_plan)
        except Exception as exc:
            log.warning("planner.llm_failed", error=str(exc)[:200])
            return rule_plan

    def rule_plan(self, question: str, default_as_of: date, explicit_as_of: bool = False) -> QueryPlan:
        as_of = default_as_of if explicit_as_of else (parse_question_date(question) or default_as_of)
        subjects = [m.id for m in self.lex.match_subjects(question)]
        attrs = [m.id for m in self.lex.match_attributes(question)]
        unknown = self.lex.unknown_products(question)
        slots: list[SlotRef] = []
        if not (unknown and not subjects):
            if not attrs and subjects and OVERVIEW_CUE.search(question):
                attrs = [a.id for a in self.lex.attributes.values() if set(a.subjects) & set(subjects)]
            for aid in attrs:
                adef = self.lex.attributes[aid]
                matched = [s for s in subjects if not adef.subjects or s in adef.subjects]
                if not matched and adef.default_subject and not unknown:
                    matched = [adef.default_subject]
                scopes = self.lex.match_scopes(question, adef.scopes) if adef.scopes else []
                scope = scopes[0] if scopes else adef.default_scope
                for sid in dict.fromkeys(matched):
                    slots.append(SlotRef(subject_id=sid, attribute=aid, scope=scope))
        intent = "unknown" if not slots else ("compare" if len(slots) > 1 else "lookup")
        if OVERVIEW_CUE.search(question) and slots:
            intent = "overview"
        return QueryPlan(
            question=question,
            intent=intent,
            as_of=as_of,
            slots=slots,
            entities=list(dict.fromkeys(subjects)),
            unknown_entities=unknown,
            sub_queries=[self._sub_query(s) for s in slots],
            claimed_values=sorted(values_in_text(question)),
        )

    def _sub_query(self, s: SlotRef) -> str:
        parts = [self.lex.subject_label(s.subject_id), self.lex.attribute_label(s.attribute)]
        if s.scope:
            parts.append(self.lex.scope_label(s.scope))
        return " ".join(parts)

    async def _llm_plan(
        self, question: str, default_as_of: date, explicit_as_of: bool, fallback: QueryPlan
    ) -> QueryPlan:
        ents, attrs, scopes = self.lex.registry_prompt()
        prompt = load_prompt("planner").render(
            entities=ents, attributes=attrs, scopes=scopes, as_of=default_as_of.isoformat(), question=question
        )
        out = await self.gateway.structured(prompt, _LLMPlan, self.model)
        slots = [
            SlotRef(
                subject_id=s.subject_id,
                attribute=s.attribute,
                scope=s.scope if s.scope in self.lex.scopes else None,
            )
            for s in out.slots
            if s.subject_id in self.lex.subjects and s.attribute in self.lex.attributes
        ]
        as_of = default_as_of if explicit_as_of else (parse_date(out.as_of) or fallback.as_of)
        return fallback.model_copy(
            update={
                "slots": slots,
                "as_of": as_of,
                "unknown_entities": out.unknown_entities or fallback.unknown_entities,
                "sub_queries": [*out.sub_queries[:4], *fallback.sub_queries]
                if slots
                else fallback.sub_queries,
                "intent": out.intent
                if out.intent in ("lookup", "compare", "overview", "unknown")
                else fallback.intent,
            }
        )
