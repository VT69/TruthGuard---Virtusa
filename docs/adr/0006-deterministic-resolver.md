# ADR 0006: Deterministic, policy-driven resolver (R0–R5)

**Status:** accepted · **Date:** 2026-10-23

## Context
Non-negotiable #2: precedence is policy, so it must be deterministic and auditable. LLMs extract and explain; they never decide.

## Decision
`resolution/engine.py` runs the rules in the order configured in `precedence.yaml` (editable at runtime via `PUT /v1/policy/precedence`):
- **Q** quarantine (always first): assertions from prompt-injection chunks never compete.
- **R0** temporal validity at the as-of date. **R1** explicit supersession, transitive, scoped to the amended clause. **R2** authority tier; a lower-tier source newer than every contradicting higher-tier source escalates to UNRESOLVED with `possible_unofficial_update`. **R3** specificity (scope split when no scope is asked). **R4** recency within a series (`inferred_supersession`; optional cross-series "recency beats authority"). **R5** no decision.
R0 and R1 always run (so stale sources are never cited); later rules stop once the evidence is consistent. Every evaluated rule is logged with what it eliminated; rule strengths (1.0 / 0.75 / 0.55) feed confidence; inferred lineage edges cap strength at 0.8.

## Consequences
Property tests (Hypothesis) prove determinism, input-order independence and rule precedence. Ablation A3 (an LLM picks the winner) is implemented for comparison but only runs with an LLM configured.
