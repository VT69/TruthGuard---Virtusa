# ADR 0010: Assertions as structured slots, extracted at ingest

**Status:** accepted · **Date:** 2026-10-23

## Context
Differentiator #1: conflicts are structured data — one slot (subject, attribute, scope) with two values — not an LLM's opinion.

## Decision
At ingest every chunk becomes assertion tuples with a normalized value (`INR:950`, `PCT:1.5`, `YEARS:2`, `MODALITY:may`), scope, modality, effective window and a verbatim quote span. A domain registry (`ingestion/lexicon.yaml`: subjects, attributes, scopes, owners) gives canonical ids. Two extractors share one validator: a rule extractor (offline/fallback) and an LLM extractor whose output is re-validated in code (registry ids, verbatim quote, parseable value). Normalization makes "1.5%", "1.50%" and "one and a half percent" the same value, so numeric drift is not a conflict.

## Consequences
Conflict detection, resolution and blend detection are exact code over slots and are unit-testable. New domains need registry entries (or the LLM extractor proposing them); facts outside the registry are not extracted.
