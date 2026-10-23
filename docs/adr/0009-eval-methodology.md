# ADR 0009: Eval data first; frozen test split; honest baseline proxies

**Status:** accepted · **Date:** 2026-10-23

## Decision
- A seeded generator (`eval/generator`) renders 42 Northwind Bank documents in five formats with **ground truth**: every fact, 11 lineage edges, 32 planted conflicts covering all 11 planted cases, and 151 gold questions (70/30 dev/test, stratified by case). Expected outcomes are **hand-written** in `questions.yaml`, never computed by the system under test.
- Tuning happens on dev only. The test split is run once for final numbers (`make eval`).
- Disclosure is read from structured output, not judged by an LLM.
- Offline, baselines B0–B2 are **deterministic proxies** of naive-RAG behaviour (top-ranked value, newest value, "sources differ" without rules). They never follow prompt injections, so they do not inflate baseline failure. Real LLM baselines and ablations A3/A4 require `LLM_MODE=live`; the report states the mode on its first line.

## Consequences
The same author wrote the corpus, the questions and the offline lexicon, so offline numbers overstate generalisation to new phrasings (one test question already fails on phrasing). The LLM planner/extractor paths and a paraphrase pass are the mitigation; see README "Limitations".
