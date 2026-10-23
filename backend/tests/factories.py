"""Small builders for hand-made resolver scenarios."""

from __future__ import annotations

from datetime import date

from truthguard.domain.enums import Provenance, Relation, ValueKind
from truthguard.domain.models import Assertion, Document, FieldMeta, LineageEdge


def doc(
    doc_id: str,
    tier: int = 2,
    eff: date | None = None,
    series: str | None = None,
    doc_type: str = "policy",
    pub: date | None = None,
    title: str | None = None,
) -> Document:
    return Document(
        id=doc_id,
        series_id=series or doc_id,
        title=title or doc_id.replace("_", " ").title(),
        doc_type=doc_type,
        authority_tier=tier,
        effective_from=eff,
        published_at=pub,
        source_uri=f"{doc_id}.md",
        sha256=doc_id * 2,
        fmt="md",
        field_provenance={"effective_from": FieldMeta(provenance=Provenance.EXPLICIT, confidence=1.0)},
    )


def assertion(
    aid: str,
    doc_id: str,
    value: str,
    scope: str = "all",
    slot: str = "s|a",
    section: list[str] | None = None,
    quarantined: bool = False,
    kind: ValueKind = ValueKind.MONEY,
) -> Assertion:
    subject, _, attr = slot.partition("|")
    return Assertion(
        id=aid,
        chunk_id=f"ch_{aid}",
        document_id=doc_id,
        subject_id=subject,
        attribute=attr,
        kind=kind,
        value_norm=value,
        value_display=value,
        scope=scope,
        quote=f"quote {aid}",
        quote_start=0,
        quote_end=5,
        section_path=section or [],
        quarantined=quarantined,
    )


def edge(
    src: str,
    dst: str,
    relation: Relation = Relation.SUPERSEDES,
    scope: str | None = None,
    confidence: float = 1.0,
) -> LineageEdge:
    return LineageEdge(
        id=f"e_{src}_{dst}",
        from_doc=src,
        to_doc=dst,
        relation=relation,
        scope_section=scope,
        evidence_span=f"{src} {relation.value} {dst}",
        provenance=Provenance.EXPLICIT,
        confidence=confidence,
    )
