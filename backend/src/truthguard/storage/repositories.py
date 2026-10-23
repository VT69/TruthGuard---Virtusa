"""Repositories: the only module that maps between ORM rows and domain models."""

from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from truthguard.domain.models import (
    Assertion,
    Chunk,
    ConflictSet,
    Document,
    FieldMeta,
    LineageEdge,
    Resolution,
)
from truthguard.ingestion.lineage import LineageStatement
from truthguard.storage.orm import (
    AssertionRow,
    ChunkRow,
    ConflictRow,
    DocumentRow,
    EdgeRow,
    JobRow,
    PolicyRow,
    TraceRow,
)


def doc_to_domain(r: DocumentRow) -> Document:
    return Document(
        id=r.id,
        series_id=r.series_id,
        title=r.title,
        doc_type=r.doc_type,
        authority_tier=r.authority_tier,
        version_label=r.version_label,
        effective_from=r.effective_from,
        effective_to=r.effective_to,
        published_at=r.published_at,
        topic=r.topic,
        source_uri=r.source_uri,
        sha256=r.sha256,
        fmt=r.fmt,
        origin="user" if r.origin == "user" else "seed",
        injection_flag=r.injection_flag,
        field_provenance={k: FieldMeta.model_validate(v) for k, v in (r.field_provenance or {}).items()},
    )


def chunk_to_domain(r: ChunkRow) -> Chunk:
    return Chunk(
        id=r.id,
        document_id=r.document_id,
        ord=r.ord,
        section_path=list(r.section_path),
        text=r.text,
        char_start=r.char_start,
        char_end=r.char_end,
        injection_flag=r.injection_flag,
    )


def edge_to_domain(r: EdgeRow) -> LineageEdge:
    return LineageEdge.model_validate(
        {
            "id": r.id,
            "from_doc": r.from_doc,
            "to_doc": r.to_doc,
            "relation": r.relation,
            "scope_section": r.scope_section,
            "evidence_span": r.evidence_span,
            "provenance": r.provenance,
            "confidence": r.confidence,
        }
    )


class CorpusRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.s = session

    async def sha_exists(self, sha: str) -> str | None:
        row = (
            await self.s.execute(select(DocumentRow.id).where(DocumentRow.sha256 == sha))
        ).scalar_one_or_none()
        return row

    async def id_exists(self, doc_id: str) -> bool:
        return (await self.s.get(DocumentRow, doc_id)) is not None

    async def add_document(
        self,
        doc: Document,
        raw_text: str,
        series_hint: str | None,
        subjects: list[str],
        statements: list[LineageStatement],
        chunks: list[Chunk],
        embeddings: list[list[float]],
        assertions: list[Assertion],
    ) -> None:
        self.s.add(
            DocumentRow(
                id=doc.id,
                series_id=doc.series_id,
                title=doc.title,
                doc_type=doc.doc_type,
                authority_tier=doc.authority_tier,
                version_label=doc.version_label,
                effective_from=doc.effective_from,
                effective_to=doc.effective_to,
                published_at=doc.published_at,
                topic=doc.topic,
                status=doc.status.value,
                source_uri=doc.source_uri,
                sha256=doc.sha256,
                fmt=doc.fmt,
                origin=doc.origin,
                injection_flag=doc.injection_flag,
                field_provenance={k: v.model_dump(mode="json") for k, v in doc.field_provenance.items()},
                series_hint=series_hint,
                subjects=subjects,
                lineage_statements=[st.model_dump(mode="json") for st in statements],
                raw_text=raw_text,
            )
        )
        await self.s.flush()
        for c, emb in zip(chunks, embeddings, strict=True):
            self.s.add(
                ChunkRow(
                    id=c.id,
                    document_id=c.document_id,
                    ord=c.ord,
                    section_path=c.section_path,
                    text=c.text,
                    char_start=c.char_start,
                    char_end=c.char_end,
                    injection_flag=c.injection_flag,
                    embedding=emb,
                )
            )
        await self.s.flush()
        for a in assertions:
            self.s.add(
                AssertionRow(
                    id=a.id,
                    chunk_id=a.chunk_id,
                    document_id=a.document_id,
                    subject_id=a.subject_id,
                    attribute=a.attribute,
                    payload=a.model_dump(mode="json"),
                )
            )

    async def update_series(self, series: dict[str, str]) -> None:
        rows = (await self.s.execute(select(DocumentRow))).scalars().all()
        for r in rows:
            if r.id in series and r.series_id != series[r.id]:
                r.series_id = series[r.id]

    async def replace_edges(self, edges: list[LineageEdge]) -> None:
        await self.s.execute(delete(EdgeRow))
        for e in edges:
            self.s.add(
                EdgeRow(
                    id=e.id,
                    from_doc=e.from_doc,
                    to_doc=e.to_doc,
                    relation=e.relation.value,
                    scope_section=e.scope_section,
                    evidence_span=e.evidence_span,
                    provenance=e.provenance.value,
                    confidence=e.confidence,
                )
            )

    async def replace_conflicts(self, items: list[tuple[ConflictSet, Resolution, str]], as_of: date) -> None:
        await self.s.execute(delete(ConflictRow))
        for c, r, severity in items:
            self.s.add(
                ConflictRow(
                    id=c.id,
                    slot_key=c.slot_key,
                    topic=c.topic,
                    severity=severity,
                    conflict=c.model_dump(mode="json"),
                    resolution=r.model_dump(mode="json"),
                    as_of=as_of,
                )
            )

    async def delete_documents(self, ids: list[str]) -> None:
        if not ids:
            return
        await self.s.execute(delete(AssertionRow).where(AssertionRow.document_id.in_(ids)))
        await self.s.execute(delete(ChunkRow).where(ChunkRow.document_id.in_(ids)))
        await self.s.execute(delete(EdgeRow).where(EdgeRow.from_doc.in_(ids) | EdgeRow.to_doc.in_(ids)))
        await self.s.execute(delete(DocumentRow).where(DocumentRow.id.in_(ids)))

    async def load_all(self) -> dict[str, Any]:
        docs = (await self.s.execute(select(DocumentRow))).scalars().all()
        chunks = (
            (await self.s.execute(select(ChunkRow).order_by(ChunkRow.document_id, ChunkRow.ord)))
            .scalars()
            .all()
        )
        assertions = (await self.s.execute(select(AssertionRow))).scalars().all()
        edges = (await self.s.execute(select(EdgeRow))).scalars().all()
        return {
            "documents": [doc_to_domain(r) for r in docs],
            "raw_text": {r.id: r.raw_text for r in docs},
            "series_hints": {r.id: r.series_hint for r in docs},
            "subjects": {r.id: list(r.subjects or []) for r in docs},
            "statements": {
                r.id: [LineageStatement.model_validate(x) for x in (r.lineage_statements or [])] for r in docs
            },
            "chunks": [chunk_to_domain(r) for r in chunks],
            "embeddings": {r.id: r.embedding for r in chunks},
            "assertions": [Assertion.model_validate(r.payload) for r in assertions],
            "edges": [edge_to_domain(r) for r in edges],
        }

    async def conflicts(self) -> list[tuple[ConflictSet, Resolution, str]]:
        rows = (await self.s.execute(select(ConflictRow))).scalars().all()
        return [
            (ConflictSet.model_validate(r.conflict), Resolution.model_validate(r.resolution), r.severity)
            for r in rows
        ]

    async def user_document_ids(self) -> list[str]:
        return list(
            (await self.s.execute(select(DocumentRow.id).where(DocumentRow.origin == "user"))).scalars()
        )


class JobRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.s = session

    async def create(self, job_id: str) -> None:
        self.s.add(JobRow(id=job_id, status="queued", stage="queued", progress={}, document_ids=[]))

    async def update(self, job_id: str, **fields: Any) -> None:
        row = await self.s.get(JobRow, job_id)
        if row is None:
            return
        for k, v in fields.items():
            setattr(row, k, v)

    async def get(self, job_id: str) -> dict[str, Any] | None:
        row = await self.s.get(JobRow, job_id)
        if row is None:
            return None
        return {
            "id": row.id,
            "status": row.status,
            "stage": row.stage,
            "progress": row.progress,
            "error": row.error,
            "document_ids": row.document_ids,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        }


class TraceRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.s = session

    async def save(
        self,
        *,
        trace_id: str,
        question: str,
        as_of: date,
        mode: str,
        status: str,
        final: dict[str, Any],
        state: dict[str, Any],
        events: list[dict[str, Any]],
        latency_ms: float,
    ) -> None:
        existing = await self.s.get(TraceRow, trace_id)
        if existing is not None:
            await self.s.delete(existing)
            await self.s.flush()
        self.s.add(
            TraceRow(
                id=trace_id,
                question=question,
                as_of=as_of,
                mode=mode,
                status=status,
                final=final,
                state=state,
                events=events,
                latency_ms=latency_ms,
            )
        )

    async def get(self, trace_id: str) -> TraceRow | None:
        return await self.s.get(TraceRow, trace_id)

    async def recent(self, limit: int = 50) -> list[TraceRow]:
        q = select(TraceRow).order_by(TraceRow.created_at.desc()).limit(limit)
        return list((await self.s.execute(q)).scalars())


class PolicyRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.s = session

    async def get(self) -> dict[str, Any] | None:
        row = await self.s.get(PolicyRow, 1)
        return dict(row.payload) if row else None

    async def put(self, payload: dict[str, Any]) -> None:
        row = await self.s.get(PolicyRow, 1)
        if row is None:
            self.s.add(PolicyRow(id=1, payload=payload))
        else:
            row.payload = payload

    async def clear(self) -> None:
        await self.s.execute(delete(PolicyRow))
