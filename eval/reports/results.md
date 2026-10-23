# TruthGuard evaluation report

> **Mode: offline.** Agents run their deterministic paths and baselines B0-B2 are deterministic *proxies* of naive-RAG behaviour (see `truthguard/agents/baselines.py`). Re-run with `LLM_MODE=live` and API keys to measure real LLM baselines and ablations A3/A4.

Split: **test** (44 questions). Generated 2026-10-23. Corpus ingest: 0.95 s for 42 documents.

## Headline

| Run | Silent-blend ↓ | Conflict F1 ↑ | Resolution acc. ↑ | Status acc. ↑ | Value acc. ↑ | Stale cites ↓ | Citation prec. ↑ | Abstention recall ↑ | p50 / p95 ms | $/query |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| **B0** | 96.8% | 0.0 | 0.0% | 29.5% | 43.6% | 41.0% | n/a | 100.0% | 0.7 / 1.2 | 0.0 |
| **B1** | 96.8% | 0.0 | 0.0% | 29.5% | 66.7% | 15.4% | n/a | 100.0% | 0.6 / 1.0 | 0.0 |
| **B2** | 6.5% | 0.9667 | 0.0% | 38.6% | 35.9% | 59.0% | n/a | 100.0% | 0.6 / 0.9 | 0.0 |
| **TG** | 0.0% | 0.9836 | 96.2% | 97.7% | 97.4% | 0.0% | 100.0% | 100.0% | 72.1 / 103.8 | 0.0 |
| **A1** | 0.0% | 0.9836 | 96.2% | 97.7% | 97.4% | 0.0% | 100.0% | 100.0% | 73.8 / 117.5 | 0.0 |
| **A2** | 0.0% | 0.9836 | 96.2% | 97.7% | 97.4% | 0.0% | 100.0% | 100.0% | 42.8 / 60.3 | 0.0 |
| A3 | skipped: requires LLM_MODE=live: offline agents have no LLM to swap | | | | | | | | | |
| A4 | skipped: requires LLM_MODE=live: offline agents have no LLM to swap | | | | | | | | | |
| **TG@k3** | 0.0% | 0.9836 | 92.3% | 97.7% | 94.9% | 2.6% | 100.0% | 100.0% | 68.8 / 112.1 | 0.0 |
| **A1@k3** | 0.0% | 0.9836 | 88.5% | 97.7% | 97.4% | 0.0% | 100.0% | 100.0% | 68.8 / 164.1 | 0.0 |
| **A2@k3** | 0.0% | 0.9836 | 88.5% | 97.7% | 94.9% | 2.6% | 100.0% | 100.0% | 29.8 / 35.8 | 0.0 |

Runs: **B0** Naive RAG: dense top-5, one answer, no conflict handling; **B1** RAG + recency: newest documents first; **B2** Self-critique: one agent drafts and critiques itself; **TG** TruthGuard full pipeline; **A1** Ablation: no lineage expansion; **A2** Ablation: no counter-retrieval in the critic; **A3** Ablation: an LLM picks the winner instead of the rules; **A4** Ablation: critic uses the synthesizer's model; **TG@k3** Stress: TruthGuard with a 3-chunk retrieval budget; **A1@k3** Stress: no lineage expansion, 3-chunk budget; **A2@k3** Stress: no counter-retrieval, 3-chunk budget

Silent-blend rate: TruthGuard 0.0% vs naive RAG 96.8% (∞ lower).

## Retrieval: is the winning document in the evidence?

| Run | Winner-document recall | Questions |
| --- | --- | --- |
| TG | 100.0% | 35 |
| A1 | 100.0% | 35 |
| A2 | 100.0% | 35 |
| TG@k3 | 100.0% | 35 |
| A1@k3 | 94.3% | 35 |
| A2@k3 | 100.0% | 35 |

## Lineage expansion vs retrieval budget

Share of conflict questions whose evidence contains the winning source / every version of the conflict, by rerank budget k.

| k | Expansion | Winner recall | All-versions recall |
| --- | --- | --- | --- |
| 3 | on | 100.0% | 96.4% |
| 3 | off | 82.1% | 67.9% |
| 5 | on | 100.0% | 100.0% |
| 5 | off | 96.4% | 92.9% |
| 8 | on | 100.0% | 100.0% |
| 8 | off | 100.0% | 100.0% |
| 12 | on | 100.0% | 100.0% |
| 12 | off | 100.0% | 100.0% |

## TruthGuard by planted case

| Case | n | Status acc. | Silent-blend |
| --- | --- | --- | --- |
| as_of | 3 | 100.0% | 0.0% |
| authority_clash | 2 | 100.0% | 0.0% |
| control | 6 | 100.0% | n/a |
| future_dated | 2 | 100.0% | 0.0% |
| genuine_tie | 2 | 100.0% | 0.0% |
| missing_metadata | 3 | 66.7% | 0.0% |
| modality_conflict | 2 | 100.0% | 0.0% |
| multi_hop | 2 | 100.0% | 0.0% |
| numeric_drift | 2 | 100.0% | n/a |
| recency_in_series | 2 | 100.0% | 0.0% |
| scope_split | 4 | 100.0% | 0.0% |
| stale_faq | 1 | 100.0% | 0.0% |
| unanswerable | 5 | 100.0% | n/a |
| version_supersession | 8 | 100.0% | 0.0% |

## TruthGuard status confusion (expected -> got)

- `INSUFFICIENT_EVIDENCE->INSUFFICIENT_EVIDENCE`: 5
- `RESOLVED_CONFLICT->INSUFFICIENT_EVIDENCE`: 1
- `RESOLVED_CONFLICT->RESOLVED_CONFLICT`: 26
- `UNRESOLVED_CONFLICT->UNRESOLVED_CONFLICT`: 4
- `VERIFIED->VERIFIED`: 8

## Where TruthGuard is wrong (1 questions)

- `q090` (missing_metadata) “Within how many working days are savings account closure requests processed?” expected RESOLVED_CONFLICT ['DAYS:7'], got INSUFFICIENT_EVIDENCE.
