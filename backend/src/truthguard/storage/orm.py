"""SQLAlchemy 2 ORM. Portable across PostgreSQL (+pgvector) and SQLite (tests, quick start)."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, ClassVar

from sqlalchemy import JSON, Boolean, Date, DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.engine import Dialect
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeDecorator, TypeEngine

from truthguard.core.clock import now

EMBED_DIM = 384


class Embedding(TypeDecorator[list[float]]):
    """pgvector `vector(384)` on PostgreSQL, JSON elsewhere."""

    impl = JSON
    cache_ok = True

    def load_dialect_impl(self, dialect: Dialect) -> TypeEngine[Any]:
        if dialect.name == "postgresql":
            from pgvector.sqlalchemy import Vector

            return dialect.type_descriptor(Vector(EMBED_DIM))
        return dialect.type_descriptor(JSON())

    def process_bind_param(self, value: list[float] | None, dialect: Dialect) -> Any:
        return None if value is None else [float(x) for x in value]

    def process_result_value(self, value: Any, dialect: Dialect) -> list[float] | None:
        if value is None:
            return None
        return [float(x) for x in value]


class Base(DeclarativeBase):
    type_annotation_map: ClassVar[dict[Any, Any]] = {dict[str, Any]: JSON, list[Any]: JSON}


class DocumentRow(Base):
    __tablename__ = "documents"
    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    series_id: Mapped[str] = mapped_column(String(128), index=True)
    title: Mapped[str] = mapped_column(String(512))
    doc_type: Mapped[str] = mapped_column(String(64))
    authority_tier: Mapped[int] = mapped_column(Integer)
    version_label: Mapped[str | None] = mapped_column(String(64), nullable=True)
    effective_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    published_at: Mapped[date | None] = mapped_column(Date, nullable=True)
    topic: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="active")
    source_uri: Mapped[str] = mapped_column(String(512))
    sha256: Mapped[str] = mapped_column(String(64), unique=True)
    fmt: Mapped[str] = mapped_column(String(16))
    origin: Mapped[str] = mapped_column(String(16), default="seed")
    injection_flag: Mapped[bool] = mapped_column(Boolean, default=False)
    field_provenance: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    series_hint: Mapped[str | None] = mapped_column(String(128), nullable=True)
    subjects: Mapped[list[Any]] = mapped_column(JSON, default=list)
    lineage_statements: Mapped[list[Any]] = mapped_column(JSON, default=list)
    raw_text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ChunkRow(Base):
    __tablename__ = "chunks"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    ord: Mapped[int] = mapped_column(Integer)
    section_path: Mapped[list[Any]] = mapped_column(JSON)
    text: Mapped[str] = mapped_column(Text)
    char_start: Mapped[int] = mapped_column(Integer)
    char_end: Mapped[int] = mapped_column(Integer)
    injection_flag: Mapped[bool] = mapped_column(Boolean, default=False)
    embedding: Mapped[list[float] | None] = mapped_column(Embedding, nullable=True)


class EdgeRow(Base):
    __tablename__ = "lineage_edges"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    from_doc: Mapped[str] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    to_doc: Mapped[str] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    relation: Mapped[str] = mapped_column(String(32))
    scope_section: Mapped[str | None] = mapped_column(String(64), nullable=True)
    evidence_span: Mapped[str] = mapped_column(Text)
    provenance: Mapped[str] = mapped_column(String(16))
    confidence: Mapped[float] = mapped_column(Float)


class AssertionRow(Base):
    __tablename__ = "assertions"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    chunk_id: Mapped[str] = mapped_column(ForeignKey("chunks.id", ondelete="CASCADE"), index=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    subject_id: Mapped[str] = mapped_column(String(64))
    attribute: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)

    __table_args__ = (Index("ix_assertions_slot", "subject_id", "attribute"),)


class ConflictRow(Base):
    __tablename__ = "conflict_sets"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    slot_key: Mapped[str] = mapped_column(String(160), index=True)
    topic: Mapped[str | None] = mapped_column(String(64), nullable=True)
    severity: Mapped[str] = mapped_column(String(16))
    conflict: Mapped[dict[str, Any]] = mapped_column(JSON)
    resolution: Mapped[dict[str, Any]] = mapped_column(JSON)
    as_of: Mapped[date] = mapped_column(Date)


class JobRow(Base):
    __tablename__ = "ingest_jobs"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    status: Mapped[str] = mapped_column(String(16))
    stage: Mapped[str] = mapped_column(String(32))
    progress: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    document_ids: Mapped[list[Any]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class TraceRow(Base):
    __tablename__ = "query_traces"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    question: Mapped[str] = mapped_column(Text)
    as_of: Mapped[date] = mapped_column(Date)
    mode: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(32))
    final: Mapped[dict[str, Any]] = mapped_column(JSON)
    state: Mapped[dict[str, Any]] = mapped_column(JSON)
    events: Mapped[list[Any]] = mapped_column(JSON)
    latency_ms: Mapped[float] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)


class PolicyRow(Base):
    __tablename__ = "precedence_policy"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)
