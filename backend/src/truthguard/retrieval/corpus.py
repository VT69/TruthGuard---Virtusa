"""In-memory snapshot of the corpus (documents, chunks, assertions, edges, indexes).

PostgreSQL/SQLite is the source of truth; this snapshot is rebuilt from it at startup and
after every ingest, so query time is lookups and vector math with no database round trips.
At demo scale (~10^2-10^3 chunks) a brute-force cosine scan takes well under a millisecond;
the pgvector HNSW index is the scale path (ADR 0005).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import numpy as np

from truthguard.domain.enums import DocStatus
from truthguard.domain.models import Assertion, Chunk, Document, LineageEdge
from truthguard.ingestion.chunk import index_text
from truthguard.ingestion.lineage import LineageStatement
from truthguard.retrieval.embed import Embedder
from truthguard.retrieval.lexical import BM25Index


@dataclass
class CorpusSnapshot:
    documents: dict[str, Document] = field(default_factory=dict)
    raw_text: dict[str, str] = field(default_factory=dict)
    chunks: dict[str, Chunk] = field(default_factory=dict)
    assertions: dict[str, Assertion] = field(default_factory=dict)
    edges: list[LineageEdge] = field(default_factory=list)
    statements: dict[str, list[LineageStatement]] = field(default_factory=dict)
    series_hints: dict[str, str | None] = field(default_factory=dict)
    subjects: dict[str, list[str]] = field(default_factory=dict)
    chunk_order: list[str] = field(default_factory=list)
    matrix: np.ndarray = field(default_factory=lambda: np.zeros((0, 1), dtype=np.float32))
    bm25: BM25Index = field(default_factory=BM25Index)
    by_chunk: dict[str, list[Assertion]] = field(default_factory=dict)
    by_doc_chunks: dict[str, list[Chunk]] = field(default_factory=dict)
    by_slot: dict[str, list[Assertion]] = field(default_factory=dict)
    version: int = 0

    @classmethod
    def build(cls, data: dict[str, Any], embedder: Embedder) -> CorpusSnapshot:
        snap = cls()
        snap.documents = {d.id: d for d in data["documents"]}
        snap.raw_text = dict(data["raw_text"])
        snap.chunks = {c.id: c for c in data["chunks"]}
        snap.assertions = {a.id: a for a in data["assertions"]}
        snap.edges = list(data["edges"])
        snap.statements = dict(data["statements"])
        snap.series_hints = dict(data["series_hints"])
        snap.subjects = dict(data["subjects"])
        snap._index(embedder, data.get("embeddings", {}))
        return snap

    def _index(self, embedder: Embedder, stored: dict[str, list[float] | None]) -> None:
        self.chunk_order = sorted(
            self.chunks, key=lambda cid: (self.chunks[cid].document_id, self.chunks[cid].ord)
        )
        self.bm25 = BM25Index()
        texts = []
        for cid in self.chunk_order:
            c = self.chunks[cid]
            doc = self.documents.get(c.document_id)
            t = index_text(doc.title if doc else "", c.section_path, c.text)
            texts.append(t)
            self.bm25.add(cid, t)
        vecs: list[np.ndarray] = []
        missing = [i for i, cid in enumerate(self.chunk_order) if not stored.get(cid)]
        fresh = embedder.embed([texts[i] for i in missing]) if missing else np.zeros((0, embedder.dim))
        fresh_by = {self.chunk_order[i]: fresh[j] for j, i in enumerate(missing)}
        for cid in self.chunk_order:
            v = stored.get(cid)
            vecs.append(np.asarray(v, dtype=np.float32) if v else fresh_by[cid])
        self.matrix = np.vstack(vecs) if vecs else np.zeros((0, embedder.dim), dtype=np.float32)
        self.by_chunk = defaultdict(list)
        self.by_slot = defaultdict(list)
        for a in sorted(self.assertions.values(), key=lambda a: a.id):
            self.by_chunk[a.chunk_id].append(a)
            self.by_slot[a.slot_key].append(a)
        self.by_doc_chunks = defaultdict(list)
        for cid in self.chunk_order:
            self.by_doc_chunks[self.chunks[cid].document_id].append(self.chunks[cid])

    # ------------------------------------------------------------------ search

    def dense_search(self, query_vec: np.ndarray, top_k: int) -> list[tuple[str, float]]:
        if self.matrix.shape[0] == 0:
            return []
        sims = self.matrix @ query_vec.astype(np.float32)
        k = min(top_k, len(sims))
        idx = np.argpartition(-sims, k - 1)[:k]
        ranked = sorted(((self.chunk_order[i], float(sims[i])) for i in idx), key=lambda x: (-x[1], x[0]))
        return ranked

    def lexical_search(self, query: str, top_k: int) -> list[tuple[str, float]]:
        return self.bm25.search(query, top_k)

    # ------------------------------------------------------------------ lineage helpers

    def series_members(self, doc_id: str) -> list[str]:
        doc = self.documents.get(doc_id)
        if doc is None:
            return []
        return sorted(d.id for d in self.documents.values() if d.series_id == doc.series_id)

    def neighbours(self, doc_id: str) -> list[tuple[str, LineageEdge]]:
        out = []
        for e in self.edges:
            if e.from_doc == doc_id:
                out.append((e.to_doc, e))
            elif e.to_doc == doc_id:
                out.append((e.from_doc, e))
        return out

    def temporal_status(self, doc_id: str, as_of: date) -> DocStatus:
        doc = self.documents[doc_id]
        if doc.injection_flag and all(c.injection_flag for c in self.by_doc_chunks.get(doc_id, [])):
            return DocStatus.QUARANTINED
        start = doc.effective_from or doc.published_at
        if start and start > as_of:
            return DocStatus.NOT_YET_EFFECTIVE
        if doc.effective_to and doc.effective_to < as_of:
            return DocStatus.EXPIRED
        for e in self.edges:
            if e.to_doc == doc_id and e.relation.is_superseding and e.scope_section is None:
                src = self.documents.get(e.from_doc)
                if src is not None:
                    s = src.effective_from or src.published_at
                    if s is None or s <= as_of:
                        return DocStatus.SUPERSEDED
        return DocStatus.ACTIVE

    def stats(self) -> dict[str, int]:
        return {
            "documents": len(self.documents),
            "chunks": len(self.chunks),
            "assertions": len(self.assertions),
            "edges": len(self.edges),
            "series": len({d.series_id for d in self.documents.values()}),
        }
