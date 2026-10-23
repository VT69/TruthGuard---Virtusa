"""Evidence analyst: groups the evidence's assertions into the plan's slots and emits typed
conflict sets. It groups and normalizes only - it never judges (that is the resolver).

The grouping is deterministic code in every mode: assertions were already normalized onto
canonical slots at ingest (rule or LLM extractor), so an LLM adds nothing here but risk.
"""

from __future__ import annotations

from truthguard.core.ids import stable_id
from truthguard.domain.models import Document, EvidenceSet, QueryPlan, SlotAnalysis, SlotRow
from truthguard.ingestion.lexicon import scopes_overlap
from truthguard.resolution.conflicts import detect_conflicts


class AnalystAgent:
    def __init__(self, topic_of: dict[str, str | None]) -> None:
        self.topic_of = topic_of

    def analyze(self, plan: QueryPlan, evidence: EvidenceSet) -> list[SlotAnalysis]:
        docs: dict[str, Document] = evidence.document_by_id()
        all_assertions = list({a.id: a for it in evidence.items for a in it.assertions}.values())
        out: list[SlotAnalysis] = []
        for slot in plan.slots:
            matching = [
                a for a in all_assertions if a.slot_key == slot.key and scopes_overlap(a.scope, slot.scope)
            ]
            clean = sorted((a for a in matching if not a.quarantined), key=lambda a: a.id)
            quarantined = sorted(a.id for a in matching if a.quarantined)
            key = f"{slot.key}#{slot.scope or '*'}"
            row = SlotRow(
                slot_key=slot.key,
                subject_id=slot.subject_id,
                attribute=slot.attribute,
                requested_scope=slot.scope,
                assertion_ids=[a.id for a in clean],
            )
            conflicts = detect_conflicts(clean, docs, evidence.edges, self.topic_of)
            conflict = conflicts[0] if conflicts else None
            if conflict is not None:
                conflict = conflict.model_copy(
                    update={
                        "id": stable_id("cf", key, *conflict.assertion_ids),
                        "assertion_ids": [a.id for a in clean],
                        "distinct_values": sorted({a.value_norm for a in clean}),
                    }
                )
            out.append(SlotAnalysis(key=key, row=row, conflict=conflict, quarantined_ids=quarantined))
        return out
