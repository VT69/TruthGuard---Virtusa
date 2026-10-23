"""Staleness: a cited chunk is stale when a supersedes/amends edge that covers its clause
comes from a document already in force on the as-of date, or when its own document is not
in force on that date."""

from __future__ import annotations

from datetime import date

from truthguard.domain.models import Chunk, Document, LineageEdge
from truthguard.ingestion.lineage import section_matches


def stale_reason(
    chunk: Chunk, doc: Document, docs: dict[str, Document], edges: list[LineageEdge], as_of: date
) -> str | None:
    start = doc.effective_from or doc.published_at
    if start and start > as_of:
        return f"{doc.label()} is not in force until {start.isoformat()}"
    if doc.effective_to and doc.effective_to < as_of:
        return f"{doc.label()} expired on {doc.effective_to.isoformat()}"
    for e in edges:
        if e.to_doc != doc.id or not e.relation.is_superseding:
            continue
        if not section_matches(e.scope_section, chunk.section_path):
            continue
        src = docs.get(e.from_doc)
        if src is None:
            continue
        s = src.effective_from or src.published_at
        if s is None or s <= as_of:
            where = f" clause {e.scope_section}" if e.scope_section else ""
            return f"superseded by {src.label()}{where} ({e.relation.value})"
    return None
