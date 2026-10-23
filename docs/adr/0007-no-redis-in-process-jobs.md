# ADR 0007: In-process background ingest jobs instead of arq + Redis

**Status:** accepted · **Date:** 2026-10-23

## Context
Uploads must not block the request; job progress must be queryable (`GET /v1/jobs/{id}`).

## Decision
`POST /v1/documents` stores a job row and runs ingest in an asyncio task inside the API process; stages and per-file timings are written to the job row. Challenge Mode uses the synchronous `POST /v1/documents/quick` fast path (one short document, ~25 ms of ingest stages plus the corpus-wide re-audit).

## Consequences
One fewer service. Jobs do not survive an API restart, and ingest shares the API's CPU. The scale path is arq workers reading the same job table; the pipeline is already a standalone class (`ingestion/pipeline.py`).
