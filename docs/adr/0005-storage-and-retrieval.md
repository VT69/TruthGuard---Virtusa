# ADR 0005: PostgreSQL + pgvector as source of truth; in-memory hybrid index at query time

**Status:** accepted · **Date:** 2026-10-23

## Context
The store must hold documents, chunks, vectors, lineage, assertions, audit results, traces and policy; retrieval needs dense + lexical search with RRF.

## Decision
- **PostgreSQL 16 + pgvector** in Compose (Alembic migration `0001`, `vector(384)` column, HNSW cosine index). **SQLite** works for tests and a no-Docker laptop start; the ORM is portable via an `Embedding` type decorator.
- At startup and after every ingest, a `CorpusSnapshot` is rebuilt in memory: BM25 (Okapi) over chunk text with title and heading path, and a numpy matrix of embeddings. `VECTOR_BACKEND=pgvector` switches dense search to `ORDER BY embedding <=> :q` on the HNSW index (`retrieval/dense.py`, covered by `tests/integration/test_postgres.py`).
- Lexical search stays in process (Postgres FTS is the alternative) so behaviour is identical on both databases.

## Consequences
At demo scale (~10² chunks) every query avoids DB round trips (~0.1 s end to end). Scale path to ~10⁶ chunks: pgvector HNSW for dense, Postgres FTS or a BM25 extension for lexical, stateless API replicas, ingest workers (ADR 0007).
