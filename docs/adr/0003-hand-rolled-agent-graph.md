# ADR 0003: A hand-rolled async state machine instead of LangGraph

**Status:** accepted · **Date:** 2026-10-23

## Context
The query path is a 7-node graph with two loops (critic → synthesizer, critic → analyst on NEW_CONFLICT), streamed node by node over SSE, persisted for replay, and re-entered for deterministic re-resolution (time travel, policy edits).

## Options
1. LangGraph `StateGraph` + Postgres checkpointer — recognisable, but adds a dependency, its own streaming model and checkpoint schema.
2. **A small explicit state machine** (`orchestration/graph.py`, ~250 lines) with typed state, an event sink and a JSON snapshot.

## Decision
Option 2. Routing rules are in one readable loop: finalize when no blocking objection; re-analyze on NEW_CONFLICT; otherwise revise; stop after 2 rounds or when blocking objections stop decreasing. Every node emits a `TraceEvent` (tokens, cost, latency) that is both streamed and stored, which is what the trace replay screen plays back.

## Consequences
Nodes are plain async methods over typed state, so porting to LangGraph later is mechanical. We give up LangGraph's built-in checkpoint/resume of interrupted runs; queries take ~0.1 s offline, so resume is not needed.
