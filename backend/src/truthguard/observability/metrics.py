"""Prometheus metrics exposed at /metrics."""

from __future__ import annotations

from prometheus_client import CollectorRegistry, Counter, Histogram

REGISTRY = CollectorRegistry(auto_describe=True)

QUERIES = Counter("truthguard_queries_total", "Queries answered", ["mode", "status"], registry=REGISTRY)
QUERY_LATENCY = Histogram(
    "truthguard_query_latency_seconds",
    "End-to-end query latency",
    ["mode"],
    registry=REGISTRY,
    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2, 4, 8, 16, 32),
)
NODE_LATENCY = Histogram(
    "truthguard_node_latency_seconds",
    "Per-node latency",
    ["node"],
    registry=REGISTRY,
    buckets=(0.005, 0.01, 0.05, 0.1, 0.5, 1, 2, 5, 10),
)
LLM_COST = Counter("truthguard_llm_cost_usd_total", "Estimated LLM spend", registry=REGISTRY)
LLM_TOKENS = Counter("truthguard_llm_tokens_total", "LLM tokens", registry=REGISTRY)
INGESTED = Counter("truthguard_documents_ingested_total", "Documents ingested", ["origin"], registry=REGISTRY)
OBJECTIONS = Counter(
    "truthguard_critic_objections_total", "Critic objections", ["type", "severity"], registry=REGISTRY
)
