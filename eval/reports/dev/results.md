# TruthGuard evaluation report

> **Mode: offline.** Agents run their deterministic paths and baselines B0-B2 are deterministic *proxies* of naive-RAG behaviour (see `truthguard/agents/baselines.py`). Re-run with `LLM_MODE=live` and API keys to measure real LLM baselines and ablations A3/A4.

Split: **dev** (107 questions). Generated 2026-10-23. Corpus ingest: 0.8 s for 42 documents.

## Headline

| Run | Silent-blend ↓ | Conflict F1 ↑ | Resolution acc. ↑ | Status acc. ↑ | Value acc. ↑ | Stale cites ↓ | Citation prec. ↑ | Abstention recall ↑ | p50 / p95 ms | $/query |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| **B0** | 100.0% | 0.0 | 0.0% | 26.2% | 45.8% | 39.6% | n/a | 100.0% | 0.7 / 1.1 | 0.0 |
| **B1** | 100.0% | 0.0 | 0.0% | 26.2% | 56.2% | 26.0% | n/a | 100.0% | 0.5 / 0.9 | 0.0 |
| **B2** | 16.5% | 0.9252 | 0.0% | 34.6% | 31.2% | 62.5% | n/a | 100.0% | 0.5 / 0.8 | 0.0 |
| **TG** | 0.0% | 1.0 | 100.0% | 100.0% | 100.0% | 2.1% | 100.0% | 100.0% | 67.3 / 108.9 | 0.0 |
| **A1** | 0.0% | 1.0 | 100.0% | 100.0% | 100.0% | 2.1% | 100.0% | 100.0% | 66.3 / 99.7 | 0.0 |
| **A2** | 0.0% | 1.0 | 100.0% | 100.0% | 100.0% | 2.1% | 100.0% | 100.0% | 34.2 / 52.1 | 0.0 |
| A3 | skipped: requires LLM_MODE=live: offline agents have no LLM to swap | | | | | | | | | |
| A4 | skipped: requires LLM_MODE=live: offline agents have no LLM to swap | | | | | | | | | |
| **TG@k3** | 0.0% | 1.0 | 100.0% | 100.0% | 100.0% | 2.1% | 100.0% | 100.0% | 67.7 / 154.1 | 0.0 |
| **A1@k3** | 0.0% | 1.0 | 95.3% | 100.0% | 100.0% | 2.1% | 100.0% | 100.0% | 74.2 / 173.6 | 0.0 |
| **A2@k3** | 2.5% | 0.9872 | 85.9% | 98.1% | 97.9% | 3.1% | 100.0% | 100.0% | 34.7 / 51.8 | 0.0 |

Runs: **B0** Naive RAG: dense top-5, one answer, no conflict handling; **B1** RAG + recency: newest documents first; **B2** Self-critique: one agent drafts and critiques itself; **TG** TruthGuard full pipeline; **A1** Ablation: no lineage expansion; **A2** Ablation: no counter-retrieval in the critic; **A3** Ablation: an LLM picks the winner instead of the rules; **A4** Ablation: critic uses the synthesizer's model; **TG@k3** Stress: TruthGuard with a 3-chunk retrieval budget; **A1@k3** Stress: no lineage expansion, 3-chunk budget; **A2@k3** Stress: no counter-retrieval, 3-chunk budget

Silent-blend rate: TruthGuard 0.0% vs naive RAG 100.0% (∞ lower).

## Retrieval: is the winning document in the evidence?

| Run | Winner-document recall | Questions |
| --- | --- | --- |
| TG | 100.0% | 85 |
| A1 | 100.0% | 85 |
| A2 | 100.0% | 85 |
| TG@k3 | 100.0% | 85 |
| A1@k3 | 89.4% | 85 |
| A2@k3 | 98.8% | 85 |

## Lineage expansion vs retrieval budget

Share of conflict questions whose evidence contains the winning source / every version of the conflict, by rerank budget k.

| k | Expansion | Winner recall | All-versions recall |
| --- | --- | --- | --- |
| 3 | on | 100.0% | 92.9% |
| 3 | off | 88.6% | 78.6% |
| 5 | on | 100.0% | 100.0% |
| 5 | off | 92.9% | 90.0% |
| 8 | on | 100.0% | 100.0% |
| 8 | off | 100.0% | 100.0% |
| 12 | on | 100.0% | 100.0% |
| 12 | off | 100.0% | 100.0% |

## TruthGuard by planted case

| Case | n | Status acc. | Silent-blend |
| --- | --- | --- | --- |
| as_of | 8 | 100.0% | 0.0% |
| authority_clash | 6 | 100.0% | 0.0% |
| control | 13 | 100.0% | n/a |
| future_dated | 5 | 100.0% | 0.0% |
| genuine_tie | 5 | 100.0% | 0.0% |
| missing_metadata | 8 | 100.0% | 0.0% |
| modality_conflict | 3 | 100.0% | 0.0% |
| multi_hop | 6 | 100.0% | 0.0% |
| numeric_drift | 4 | 100.0% | n/a |
| recency_in_series | 5 | 100.0% | 0.0% |
| scope_split | 11 | 100.0% | 0.0% |
| stale_faq | 3 | 100.0% | 0.0% |
| unanswerable | 11 | 100.0% | n/a |
| version_supersession | 19 | 100.0% | 0.0% |

## TruthGuard status confusion (expected -> got)

- `INSUFFICIENT_EVIDENCE->INSUFFICIENT_EVIDENCE`: 11
- `RESOLVED_CONFLICT->RESOLVED_CONFLICT`: 67
- `UNRESOLVED_CONFLICT->UNRESOLVED_CONFLICT`: 12
- `VERIFIED->VERIFIED`: 17

## Where TruthGuard is wrong (0 questions)

None on this split.
