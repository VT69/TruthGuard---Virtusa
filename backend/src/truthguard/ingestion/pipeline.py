"""Ingest pipeline: parse -> metadata -> lineage statements -> chunk -> index -> assertions,
then a corpus-wide finalize step (edge resolution, series, conflict audit).

Idempotent: documents are keyed by content hash, so re-uploading an identical file is a
no-op. Stage timings are reported for the job progress API and Challenge Mode.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from truthguard.core.clock import today
from truthguard.core.ids import sha256_hex, slugify
from truthguard.core.logging import get_logger
from truthguard.domain.enums import JobStage, Provenance
from truthguard.domain.models import (
    Assertion,
    Chunk,
    ConflictSet,
    Document,
    FieldMeta,
    LineageEdge,
    Resolution,
)
from truthguard.ingestion.assertions import LLMExtractor, RuleExtractor
from truthguard.ingestion.chunk import chunk_document, index_text
from truthguard.ingestion.lexicon import Lexicon
from truthguard.ingestion.lineage import assign_series, extract_statements, resolve_edges
from truthguard.ingestion.metadata import extract_metadata, parse_date, series_key
from truthguard.ingestion.parse import parse_bytes
from truthguard.llm.gateway import LLMGateway
from truthguard.resolution.conflicts import detect_conflicts, severity_after
from truthguard.resolution.engine import Resolver
from truthguard.resolution.policy import PrecedencePolicy
from truthguard.retrieval.embed import Embedder
from truthguard.storage.db import Database
from truthguard.storage.repositories import CorpusRepository

log = get_logger(__name__)

ProgressFn = Callable[[JobStage, dict[str, object]], Awaitable[None]]


@dataclass
class IngestResult:
    doc_id: str
    filename: str
    skipped: bool = False
    timings_ms: dict[str, float] = field(default_factory=dict)
    assertions: int = 0
    chunks: int = 0
    injection_flag: bool = False


@dataclass
class Overrides:
    """Metadata supplied by the caller (e.g. Challenge Mode's quick memo form)."""

    title: str | None = None
    doc_type: str | None = None
    effective_from: date | None = None
    published_at: date | None = None


class IngestPipeline:
    def __init__(
        self,
        db: Database,
        lexicon: Lexicon,
        embedder: Embedder,
        gateway: LLMGateway,
        extract_model: str,
        policy_fn: Callable[[], PrecedencePolicy],
    ) -> None:
        self.db = db
        self.lex = lexicon
        self.embedder = embedder
        self.gateway = gateway
        self.extract_model = extract_model
        self.policy_fn = policy_fn
        self.rules = RuleExtractor(lexicon)

    async def ingest_file(
        self,
        filename: str,
        data: bytes,
        origin: str = "seed",
        overrides: Overrides | None = None,
        progress: ProgressFn | None = None,
    ) -> IngestResult:
        async def report(stage: JobStage, **info: object) -> None:
            if progress is not None:
                await progress(stage, {"filename": filename, **info})

        timings: dict[str, float] = {}
        t0 = time.perf_counter()

        def lap(name: str) -> None:
            nonlocal t0
            now = time.perf_counter()
            timings[name] = round((now - t0) * 1000, 2)
            t0 = now

        sha = sha256_hex(data)
        async with self.db.session() as s:
            existing = await CorpusRepository(s).sha_exists(sha)
        if existing:
            return IngestResult(doc_id=existing, filename=filename, skipped=True)

        await report(JobStage.PARSE)
        parsed = parse_bytes(filename, data)
        lap("parse")

        await report(JobStage.METADATA)
        policy = self.policy_fn()
        meta = extract_metadata(parsed, filename, policy.tiers, policy.default_tier)
        prov = dict(meta.field_provenance)
        if overrides:
            for name, value in (
                ("title", overrides.title),
                ("doc_type", overrides.doc_type),
                ("effective_from", overrides.effective_from),
                ("published", overrides.published_at),
            ):
                if value is not None:
                    prov[name] = FieldMeta(provenance=Provenance.EXPLICIT, confidence=1.0)
            meta = meta.model_copy(
                update={
                    "title": overrides.title or meta.title,
                    "doc_type": overrides.doc_type or meta.doc_type,
                    "authority_tier": policy.tier_for(overrides.doc_type)
                    if overrides.doc_type
                    else meta.authority_tier,
                    "effective_from": overrides.effective_from or meta.effective_from,
                    "published_at": overrides.published_at or meta.published_at,
                    "field_provenance": prov,
                }
            )
        lap("metadata")

        doc_id = meta.doc_key or slugify(Path(filename).stem)
        async with self.db.session() as s:
            if await CorpusRepository(s).id_exists(doc_id):
                doc_id = f"{doc_id}_{sha[:6]}"
        topic = meta.topic
        if topic is None:
            subj = self.lex.match_subjects(meta.title + " " + parsed.raw_text[:2000])
            topic = self.lex.topic_of(subj[0].id) if subj else None
        doc = Document(
            id=doc_id,
            series_id=meta.series_hint or series_key(meta.title),
            title=meta.title,
            doc_type=meta.doc_type,
            authority_tier=meta.authority_tier,
            version_label=meta.version_label,
            effective_from=meta.effective_from,
            effective_to=meta.effective_to,
            published_at=meta.published_at,
            topic=topic,
            source_uri=filename,
            sha256=sha,
            fmt=parsed.fmt,
            origin="user" if origin == "user" else "seed",
            field_provenance=meta.field_provenance,
        )

        await report(JobStage.LINEAGE)
        statements = extract_statements(parsed.raw_text, meta.supersedes_hint)
        lap("lineage")

        await report(JobStage.CHUNK)
        chunks = chunk_document(doc_id, parsed)
        doc = doc.model_copy(update={"injection_flag": any(c.injection_flag for c in chunks)})
        lap("chunk")

        await report(JobStage.INDEX)
        vectors: Any = (
            self.embedder.embed([index_text(doc.title, c.section_path, c.text) for c in chunks])
            if chunks
            else []
        )
        embeddings = [list(map(float, v)) for v in vectors]
        lap("index")

        await report(JobStage.ASSERTIONS)
        assertions: list[Assertion] = []
        for c in chunks:
            assertions.extend(await self._extract(c, doc, meta.subjects))
        lap("assertions")

        async with self.db.session() as s:
            await CorpusRepository(s).add_document(
                doc,
                parsed.raw_text,
                meta.series_hint,
                meta.subjects,
                statements,
                chunks,
                embeddings,
                assertions,
            )
        lap("store")
        log.info(
            "ingest.document",
            doc=doc_id,
            chunks=len(chunks),
            assertions=len(assertions),
            injection=doc.injection_flag,
        )
        return IngestResult(
            doc_id=doc_id,
            filename=filename,
            timings_ms=timings,
            assertions=len(assertions),
            chunks=len(chunks),
            injection_flag=doc.injection_flag,
        )

    async def _extract(self, chunk_: Chunk, doc: Document, subjects: list[str]) -> list[Assertion]:
        if self.gateway.live:
            try:
                return await LLMExtractor(self.lex, self.gateway, self.extract_model).extract(chunk_, doc)
            except Exception as exc:  # fail closed for the LLM path, fall back to rules
                log.warning("ingest.llm_extract_failed", chunk=chunk_.id, error=str(exc)[:200])
        return self.rules.extract(chunk_, doc, subjects)

    async def finalize(self, as_of: date | None = None) -> dict[str, int]:
        """Corpus-wide pass: resolve lineage targets, assign series, run the conflict audit."""
        as_of = as_of or today()
        async with self.db.session() as s:
            repo = CorpusRepository(s)
            data = await repo.load_all()
            docs: list[Document] = data["documents"]
            edges = resolve_edges(data["statements"], docs)
            series = assign_series(docs, edges, data["series_hints"])
            await repo.update_series(series)
            await repo.replace_edges(edges)
            docs = [d.model_copy(update={"series_id": series[d.id]}) for d in docs]
            audit = run_audit(
                data["assertions"], {d.id: d for d in docs}, edges, self.policy_fn(), as_of, self.lex
            )
            await repo.replace_conflicts(audit, as_of)
        return {"edges": len(edges), "conflicts": len(audit), "documents": len(docs)}


def run_audit(
    assertions: list[Assertion],
    docs: dict[str, Document],
    edges: list[LineageEdge],
    policy: PrecedencePolicy,
    as_of: date,
    lex: Lexicon,
) -> list[tuple[ConflictSet, Resolution, str]]:
    topic_of = {sid: s.topic for sid, s in lex.subjects.items()}
    resolver = Resolver(policy)
    by_id = {a.id: a for a in assertions}
    out = []
    for c in detect_conflicts(assertions, docs, edges, topic_of):
        members = [by_id[i] for i in c.assertion_ids]
        res = resolver.resolve(c.id, c.slot_key, members, docs, edges, as_of)
        out.append((c, res, severity_after(c, res).value))
    return out


def coerce_date(value: str | None) -> date | None:
    return parse_date(value) if value else None
