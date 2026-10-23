"""Approximate per-model pricing (USD per 1M tokens) and per-trace usage metering.

Prices are indicative list prices used only for the cost ticker and eval report;
check them against your provider before quoting. Unknown or local models cost 0.
"""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass, field

from truthguard.domain.models import Usage

PRICES_PER_MTOK: dict[str, tuple[float, float]] = {
    "groq/llama-3.1-8b-instant": (0.05, 0.08),
    "groq/llama-3.3-70b-versatile": (0.59, 0.79),
    "gemini/gemini-2.0-flash": (0.10, 0.40),
    "gemini/gemini-2.5-flash": (0.30, 2.50),
    "openai/gpt-4o-mini": (0.15, 0.60),
    "anthropic/claude-haiku-4-5": (1.00, 5.00),
}


def estimate_cost(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    if model.startswith("ollama/") or model.startswith("fake/"):
        return 0.0
    pin, pout = PRICES_PER_MTOK.get(model, (0.0, 0.0))
    return (prompt_tokens * pin + completion_tokens * pout) / 1_000_000


@dataclass
class UsageMeter:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0
    llm_calls: int = 0
    cache_hits: int = 0
    by_node: dict[str, Usage] = field(default_factory=dict)

    def record(self, node: str, model: str, prompt_tokens: int, completion_tokens: int, cached: bool) -> None:
        cost = 0.0 if cached else estimate_cost(model, prompt_tokens, completion_tokens)
        self.prompt_tokens += prompt_tokens
        self.completion_tokens += completion_tokens
        self.cost_usd += cost
        self.llm_calls += 1
        self.cache_hits += int(cached)
        u = self.by_node.setdefault(node, Usage())
        u.prompt_tokens += prompt_tokens
        u.completion_tokens += completion_tokens
        u.cost_usd += cost
        u.llm_calls += 1
        u.cache_hits += int(cached)

    def snapshot(self, latency_ms: float = 0.0) -> Usage:
        return Usage(
            prompt_tokens=self.prompt_tokens,
            completion_tokens=self.completion_tokens,
            cost_usd=round(self.cost_usd, 6),
            llm_calls=self.llm_calls,
            cache_hits=self.cache_hits,
            latency_ms=latency_ms,
        )


current_meter: ContextVar[UsageMeter | None] = ContextVar("current_meter", default=None)
current_node: ContextVar[str] = ContextVar("current_node", default="unknown")
