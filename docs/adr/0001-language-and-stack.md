# ADR 0001: Python 3.12 + FastAPI backend, Next.js frontend, one Postgres

**Status:** accepted · **Date:** 2026-10-23

## Context
The spec proposes Python, FastAPI, Pydantic v2, PostgreSQL + pgvector and Next.js, with Java + Spring AI as an alternative. The agent, NLP and eval ecosystem lives in Python; judges expect a typed API and a polished UI; fewer moving parts mean fewer demo-day failures.

## Options
1. **Python 3.12 / FastAPI / Pydantic v2 + Next.js 15** — typed contracts end to end, native SSE, OpenAPI for free.
2. Java 21 / Spring AI — stronger for the team, thinner agent tooling, slower iteration on eval code.
3. Single Next.js app with TypeScript agents — one language, but weak PDF/DOCX/NLP tooling.

## Decision
Option 1. `uv` manages the Python environment; `mypy --strict` and `ruff` gate the backend; `tsc --strict` and ESLint gate the UI.

## Consequences
Two runtimes to install (`make install`), mitigated by Docker Compose (`make up`).
