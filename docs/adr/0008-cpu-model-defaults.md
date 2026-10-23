# ADR 0008: CPU-only, download-free model defaults

**Status:** accepted · **Date:** 2026-10-23

## Context
Target: a 16 GB laptop with no GPU; the demo must start without network access to model hubs.

## Decision
Defaults are deterministic and dependency-light, each behind a protocol with a heavier drop-in:
| Role | Default | Drop-in (`pip install .[models]`) |
| --- | --- | --- |
| Embeddings | feature-hashed word/bigram/char-trigram vectors (384-d) | bge-small via sentence-transformers, or any OpenAI-compatible endpoint |
| Reranker | lexical cross-scorer with registry (subject/attribute) agreement | ms-marco-MiniLM cross-encoder |
| NLI | value/attribute/subject heuristic with contradiction on value clash | nli-deberta-v3-xsmall cross-encoder |

## Consequences
Retrieval quality on paraphrase-heavy corpora will be lower than with learned embeddings; the eval harness makes the swap measurable.
