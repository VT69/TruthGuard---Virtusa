"""Hybrid retrieval with mandatory lineage expansion.

1. dense top-40 and lexical (BM25) top-40 per query, fused with reciprocal rank fusion (k=60)
2. rerank the fused pool, keep the top 12 with MMR-style version diversity across series
3. lineage expansion: for every kept chunk, pull the matching section from the other
   versions in its series and from both ends of its lineage edges - this is what puts the
   2025 amendment next to the 2023 policy even when it shares few words with the question
4. join the pre-extracted assertions and mark each item's temporal status at the as-of date
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from truthguard.domain.enums import DocStatus
from truthguard.domain.models import Chunk, EvidenceItem, EvidenceSet, QueryPlan
from truthguard.ingestion.lexicon import Lexicon
from truthguard.retrieval.corpus import CorpusSnapshot
from truthguard.retrieval.dense import DenseIndex, NumpyDenseIndex
from truthguard.retrieval.embed import Embedder
from truthguard.retrieval.lexical import tokenize
from truthguard.retrieval.rerank import Reranker


@dataclass
class RetrievalConfig:
    dense_top_k: int = 40
    lexical_top_k: int = 40
    rrf_k: int = 60
    pool: int = 60
    top_k: int = 12
    diversity_penalty: float = 0.15
    lineage_expansion: bool = True


def rrf(rankings: list[list[tuple[str, float]]], k: int) -> dict[str, float]:
    fused: dict[str, float] = defaultdict(float)
    for ranking in rankings:
        for rank, (cid, _) in enumerate(ranking):
            fused[cid] += 1.0 / (k + rank + 1)
    return dict(fused)


class Retriever:
    def __init__(
        self,
        corpus: CorpusSnapshot,
        embedder: Embedder,
        reranker: Reranker,
        lexicon: Lexicon,
        config: RetrievalConfig | None = None,
        dense: DenseIndex | None = None,
    ) -> None:
        self.corpus = corpus
        self.embedder = embedder
        self.reranker = reranker
        self.lex = lexicon
        self.config = config or RetrievalConfig()
        self.dense = dense or NumpyDenseIndex(corpus)

    async def search(
        self, queries: list[str], top_k: int, rerank_query: str | None = None
    ) -> list[tuple[str, float, list[str]]]:
        cfg = self.config
        rankings: list[list[tuple[str, float]]] = []
        sources: dict[str, set[str]] = defaultdict(set)
        vecs = self.embedder.embed(queries)
        for q, v in zip(queries, vecs, strict=True):
            dense = await self.dense.search(v, cfg.dense_top_k)
            lexical = self.corpus.lexical_search(q, cfg.lexical_top_k)
            for cid, _ in dense:
                sources[cid].add("dense")
            for cid, _ in lexical:
                sources[cid].add("lexical")
            rankings += [dense, lexical]
        fused = rrf(rankings, cfg.rrf_k)
        pool = sorted(fused.items(), key=lambda x: (-x[1], x[0]))[: cfg.pool]
        if not pool:
            return []
        rq = rerank_query or queries[0]
        texts = [self._passage(cid) for cid, _ in pool]
        rr = self.reranker.score(rq, texts)
        max_f = max(f for _, f in pool)
        scored = [(cid, 0.8 * r + 0.2 * f / max_f) for (cid, f), r in zip(pool, rr, strict=True)]
        scored.sort(key=lambda x: (-x[1], x[0]))
        chosen: list[tuple[str, float, list[str]]] = []
        series_count: dict[str, int] = defaultdict(int)
        remaining = list(scored)
        while remaining and len(chosen) < top_k:
            best_i, best_s = 0, float("-inf")
            for i, (cid, s) in enumerate(remaining):
                doc = self.corpus.documents[self.corpus.chunks[cid].document_id]
                adj = s - cfg.diversity_penalty * series_count[doc.series_id]
                if adj > best_s:
                    best_i, best_s = i, adj
            cid, s = remaining.pop(best_i)
            doc = self.corpus.documents[self.corpus.chunks[cid].document_id]
            series_count[doc.series_id] += 1
            chosen.append((cid, round(s, 4), sorted(sources[cid])))
        return chosen

    def _passage(self, cid: str) -> str:
        c = self.corpus.chunks[cid]
        doc = self.corpus.documents[c.document_id]
        return f"{doc.title} {' '.join(c.section_path)} {c.text}"

    async def retrieve(
        self, plan: QueryPlan, *, lineage_expansion: bool | None = None, top_k: int | None = None
    ) -> EvidenceSet:
        expand = self.config.lineage_expansion if lineage_expansion is None else lineage_expansion
        k = top_k or self.config.top_k
        queries = [plan.question, *plan.sub_queries]
        slot_labels = " ".join(self.lex.slot_label(s.key) for s in plan.slots)
        hits = await self.search(queries, k, rerank_query=f"{plan.question} {slot_labels}".strip())
        items: dict[str, EvidenceItem] = {}
        for cid, score, srcs in hits:
            items[cid] = self._item(cid, score, srcs, plan)
        lineage_added: list[str] = []
        if expand:
            for cid in list(items):
                for extra in self._expand(self.corpus.chunks[cid], plan):
                    if extra.id not in items:
                        items[extra.id] = self._item(extra.id, 0.0, ["lineage"], plan)
                        lineage_added.append(extra.id)
        doc_ids = {it.document.id for it in items.values()}
        edges = [e for e in self.corpus.edges if e.from_doc in doc_ids or e.to_doc in doc_ids]
        return EvidenceSet(
            items=sorted(items.values(), key=lambda it: (-it.score, it.chunk.id)),
            edges=edges,
            diagnostics={
                "retrieved": len(hits),
                "lineage_added": len(lineage_added),
                "lineage_chunks": lineage_added,
                "documents": sorted(doc_ids),
            },
        )

    def _item(self, cid: str, score: float, sources: list[str], plan: QueryPlan) -> EvidenceItem:
        chunk = self.corpus.chunks[cid]
        doc = self.corpus.documents[chunk.document_id]
        status = (
            DocStatus.QUARANTINED if chunk.injection_flag else self.corpus.temporal_status(doc.id, plan.as_of)
        )
        return EvidenceItem(
            chunk=chunk,
            document=doc,
            score=score,
            sources=sources,
            temporal_status=status,
            assertions=list(self.corpus.by_chunk.get(cid, [])),
        )

    def _expand(self, chunk: Chunk, plan: QueryPlan) -> list[Chunk]:
        """Matching sections from series siblings and lineage neighbours."""
        related = set(self.corpus.series_members(chunk.document_id))
        related |= {d for d, _ in self.corpus.neighbours(chunk.document_id)}
        related.discard(chunk.document_id)
        slots = {a.slot_key for a in self.corpus.by_chunk.get(chunk.id, [])}
        slots |= {s.key for s in plan.slots}
        heading_toks = set(tokenize(" ".join(chunk.section_path[1:])))
        out: list[Chunk] = []
        for doc_id in sorted(related):
            candidates = self.corpus.by_doc_chunks.get(doc_id, [])
            same_slot = [
                c for c in candidates if any(a.slot_key in slots for a in self.corpus.by_chunk.get(c.id, []))
            ]
            if same_slot:
                out.extend(same_slot)
                continue
            if not heading_toks:
                continue
            best = max(
                candidates,
                key=lambda c: len(heading_toks & set(tokenize(" ".join(c.section_path[1:])))),
                default=None,
            )
            if best is not None and heading_toks & set(tokenize(" ".join(best.section_path[1:]))):
                out.append(best)
        return out
