"""Dense vector search backends (VECTOR_BACKEND).

numpy     brute-force cosine over the in-memory snapshot matrix (default; identical on
          SQLite and PostgreSQL; sub-millisecond at demo scale)
pgvector  PostgreSQL `ORDER BY embedding <=> :q` served by the HNSW index created by the
          Alembic migration - the scale path for ~10^6 chunks
"""

from __future__ import annotations

from typing import Protocol

import numpy as np
from sqlalchemy import text

from truthguard.retrieval.corpus import CorpusSnapshot
from truthguard.storage.db import Database


class DenseIndex(Protocol):
    async def search(self, query_vec: np.ndarray, top_k: int) -> list[tuple[str, float]]: ...


class NumpyDenseIndex:
    def __init__(self, corpus: CorpusSnapshot) -> None:
        self.corpus = corpus

    async def search(self, query_vec: np.ndarray, top_k: int) -> list[tuple[str, float]]:
        return self.corpus.dense_search(query_vec, top_k)


class PgVectorDenseIndex:
    def __init__(self, db: Database) -> None:
        if not db.is_postgres:
            raise ValueError("VECTOR_BACKEND=pgvector requires a PostgreSQL DATABASE_URL")
        self.db = db

    async def search(self, query_vec: np.ndarray, top_k: int) -> list[tuple[str, float]]:
        literal = "[" + ",".join(f"{x:.6f}" for x in query_vec.tolist()) + "]"
        sql = text(
            "SELECT id, 1 - (embedding <=> CAST(:q AS vector)) AS sim FROM chunks "
            "WHERE embedding IS NOT NULL ORDER BY embedding <=> CAST(:q AS vector), id LIMIT :k"
        )
        async with self.db.session() as s:
            rows = (await s.execute(sql, {"q": literal, "k": top_k})).all()
        return [(str(r[0]), float(r[1])) for r in rows]
