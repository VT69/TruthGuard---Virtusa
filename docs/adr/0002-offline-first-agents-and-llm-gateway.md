# ADR 0002: Offline-first agents behind one LLM gateway

**Status:** accepted · **Date:** 2026-10-23

## Context
Every agent must work in CI without API keys (non-negotiable #8), the demo must survive a provider outage, and every LLM call must be validated, cached, retried, metered and fail closed (non-negotiable #5).

## Options
1. LLM-only agents with recorded fixtures for CI.
2. **Dual-path agents**: each agent has a deterministic path (lexicon/rules/templates) and an LLM path; `LLM_MODE=offline|live|replay` selects it, and the live path falls back to the deterministic one on any failure.
3. LiteLLM SDK as the gateway.

## Decision
Option 2, with our own `llm/gateway.py`: Pydantic-validated structured output, one repair retry then `StructuredOutputError` (fail closed), per-call timeout, exponential backoff, provider fallback chain with per-provider circuit breakers, a file cache keyed by `sha256(model, messages, schema)` (also the DEMO replay store), token/cost metering per trace node. All OpenAI-compatible providers (Groq, Gemini, OpenAI, Anthropic, Ollama) share one ~40-line httpx client, so LiteLLM is optional (`pip install .[llm]`) rather than required.

## Consequences
- The deterministic paths are an honest, testable floor; eval reports state which mode produced them.
- The LLM paths are exercised in tests with `FakeProvider` (including a full critic revision loop) but have **not** been run against real providers in this repository; live numbers must be produced with keys (`LLM_MODE=live make eval`).
- The resolver never has an LLM path (ADR 0006).
