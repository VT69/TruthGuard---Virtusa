"""Group assertions into slots and detect typed conflict sets (pure, no I/O).

The evidence analyst groups and normalizes only; it never judges. Judgement is the
resolver's job.
"""

from __future__ import annotations

from collections import defaultdict

from truthguard.core.ids import stable_id
from truthguard.domain.enums import ConflictType, ResolutionStatus, RuleId, Severity, ValueKind
from truthguard.domain.models import Assertion, ConflictSet, Document, LineageEdge, Resolution
from truthguard.ingestion.normalize import parse_value_norm
from truthguard.resolution.engine import overlap

TYPE_PRIORITY = [
    ConflictType.MODALITY_CONFLICT,
    ConflictType.TEMPORAL_SUPERSESSION,
    ConflictType.SCOPE_OVERLAP,
    ConflictType.AUTHORITY_CLASH,
    ConflictType.NUMERIC_DRIFT,
    ConflictType.VALUE_MISMATCH,
]


def group_by_slot(assertions: list[Assertion]) -> dict[str, list[Assertion]]:
    groups: dict[str, list[Assertion]] = defaultdict(list)
    for a in assertions:
        groups[a.slot_key].append(a)
    return dict(groups)


def conflicting_pairs(items: list[Assertion]) -> list[tuple[Assertion, Assertion]]:
    items = sorted(items, key=lambda a: a.id)
    return [
        (a, b)
        for i, a in enumerate(items)
        for b in items[i + 1 :]
        if a.value_norm != b.value_norm and overlap(a.scope, b.scope)
    ]


def _pair_type(
    a: Assertion, b: Assertion, docs: dict[str, Document], linked: set[frozenset[str]]
) -> ConflictType:
    if a.kind == ValueKind.MODALITY:
        return ConflictType.MODALITY_CONFLICT
    da, db = docs.get(a.document_id), docs.get(b.document_id)
    if frozenset((a.document_id, b.document_id)) in linked or (da and db and da.series_id == db.series_id):
        return ConflictType.TEMPORAL_SUPERSESSION
    if (a.scope == "all") != (b.scope == "all"):
        return ConflictType.SCOPE_OVERLAP
    if da and db and da.authority_tier != db.authority_tier:
        return ConflictType.AUTHORITY_CLASH
    _, na = parse_value_norm(a.value_norm)
    _, nb = parse_value_norm(b.value_norm)
    if (
        na is not None
        and nb is not None
        and max(abs(na), abs(nb)) > 0
        and abs(na - nb) / max(abs(na), abs(nb)) < 0.05
    ):
        return ConflictType.NUMERIC_DRIFT
    return ConflictType.VALUE_MISMATCH


def detect_conflicts(
    assertions: list[Assertion],
    docs: dict[str, Document],
    edges: list[LineageEdge],
    topic_of: dict[str, str | None] | None = None,
) -> list[ConflictSet]:
    linked = {frozenset((e.from_doc, e.to_doc)) for e in edges}
    out: list[ConflictSet] = []
    for slot_key, items in sorted(group_by_slot([a for a in assertions if not a.quarantined]).items()):
        pairs = conflicting_pairs(items)
        if not pairs:
            continue
        types = {_pair_type(a, b, docs, linked) for a, b in pairs}
        ordered = [t for t in TYPE_PRIORITY if t in types]
        involved = sorted({x.id for p in pairs for x in p})
        # keep every assertion of the slot whose scope touches the conflicting ones
        touched_scopes = {x.scope for p in pairs for x in p}
        members = sorted(a.id for a in items if any(overlap(a.scope, s) for s in touched_scopes))
        values = sorted({a.value_norm for a in items if a.id in set(members)})
        kind = items[0].kind
        severity = (
            Severity.MINOR
            if ordered[0] == ConflictType.NUMERIC_DRIFT
            else (
                Severity.MAJOR
                if kind in (ValueKind.MONEY, ValueKind.PERCENT, ValueKind.DAYS, ValueKind.MODALITY)
                else Severity.MINOR
            )
        )
        subject, _, attribute = slot_key.partition("|")
        out.append(
            ConflictSet(
                id=stable_id("cf", slot_key, *involved),
                slot_key=slot_key,
                subject_id=subject,
                attribute=attribute,
                assertion_ids=members,
                distinct_values=values,
                conflict_type=ordered[0],
                secondary_types=ordered[1:],
                severity=severity,
                topic=(topic_of or {}).get(subject),
            )
        )
    return out


def severity_after(conflict: ConflictSet, resolution: Resolution) -> Severity:
    if resolution.status == ResolutionStatus.UNRESOLVED:
        return Severity.CRITICAL
    used = set(resolution.eliminated_by.values())
    if used & {RuleId.R2, RuleId.R4} or "inferred_edge" in resolution.flags:
        return Severity.MAJOR
    return Severity.MINOR if conflict.severity != Severity.CRITICAL else Severity.MAJOR
