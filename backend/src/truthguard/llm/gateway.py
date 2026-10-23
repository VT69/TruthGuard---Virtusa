"""The single entry point for every LLM call (non-negotiable #5).

Responsibilities: structured, Pydantic-validated output; one repair retry then fail
closed; per-call timeout; retries with exponential backoff; a provider fallback chain
guarded by per-provider circuit breakers; a response cache keyed by model and prompt
hash; token and cost accounting attributed to the current trace node.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ValidationError

from truthguard.core.errors import (
    OfflineModeError,
    ProviderUnavailableError,
    ReplayMissError,
    StructuredOutputError,
)
from truthguard.core.logging import get_logger
from truthguard.llm.cache import ResponseCache, cache_key
from truthguard.llm.cost import current_meter, current_node
from truthguard.llm.prompts import RenderedPrompt
from truthguard.llm.providers import (
    FALLBACK_MODELS,
    Provider,
    ProviderRequest,
    ProviderResponse,
    split_model,
)

log = get_logger(__name__)

LLMMode = Literal["offline", "live", "replay"]


@dataclass
class CircuitBreaker:
    failure_threshold: int = 3
    reset_after_s: float = 60.0
    failures: int = 0
    opened_at: float | None = None

    def allow(self) -> bool:
        if self.opened_at is None:
            return True
        if time.monotonic() - self.opened_at >= self.reset_after_s:
            self.opened_at = None  # half-open: let one call through
            self.failures = self.failure_threshold - 1
            return True
        return False

    def success(self) -> None:
        self.failures = 0
        self.opened_at = None

    def failure(self) -> None:
        self.failures += 1
        if self.failures >= self.failure_threshold:
            self.opened_at = time.monotonic()


_JSON_FENCE = re.compile(r"```(?:json)?\s*(\{.*\})\s*```", re.DOTALL)


def extract_json(text: str) -> Any:
    """Parse a JSON object from model output, tolerating code fences and leading prose."""
    text = text.strip()
    m = _JSON_FENCE.search(text)
    if m:
        text = m.group(1)
    if not text.startswith("{"):
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            text = text[start : end + 1]
    return json.loads(text)


class LLMGateway:
    def __init__(
        self,
        providers: dict[str, Provider],
        cache: ResponseCache,
        mode: LLMMode = "offline",
        fallback_order: list[str] | None = None,
        timeout_s: float = 30.0,
        max_retries: int = 2,
        backoff_base_s: float = 0.5,
    ) -> None:
        self.providers = providers
        self.cache = cache
        self.mode: LLMMode = mode
        self.fallback_order = fallback_order or list(providers)
        self.timeout_s = timeout_s
        self.max_retries = max_retries
        self.backoff_base_s = backoff_base_s
        self.breakers: dict[str, CircuitBreaker] = {name: CircuitBreaker() for name in providers}

    @property
    def live(self) -> bool:
        """True when agents should use LLM paths (live calls or cached replay)."""
        return self.mode in ("live", "replay")

    def model_chain(self, model: str) -> list[str]:
        primary_provider, _ = split_model(model)
        chain = [model]
        for name in self.fallback_order:
            if name != primary_provider and name in self.providers and name in FALLBACK_MODELS:
                chain.append(FALLBACK_MODELS[name])
        return chain

    async def structured[T: BaseModel](
        self,
        prompt: RenderedPrompt,
        schema: type[T],
        model: str,
        temperature: float = 0.0,
        use_fallback: bool = True,
    ) -> T:
        if self.mode == "offline":
            raise OfflineModeError("LLM_MODE=offline: agents must use their deterministic path")
        json_schema = schema.model_json_schema()
        messages = prompt.messages(json_schema)
        chain = self.model_chain(model) if use_fallback else [model]
        last_error: Exception | None = None
        for candidate in chain:
            provider_name, _ = split_model(candidate)
            provider = self.providers.get(provider_name)
            if self.mode == "live" and provider is None:
                continue
            breaker = self.breakers.setdefault(provider_name, CircuitBreaker())
            if self.mode == "live" and not breaker.allow():
                log.warning("llm.circuit_open", provider=provider_name)
                continue
            try:
                result = await self._structured_once(
                    candidate, provider, messages, schema, json_schema, temperature
                )
            except StructuredOutputError:
                raise  # fail closed: schema failures are not retried on other providers
            except ReplayMissError as exc:
                last_error = exc
                continue
            except Exception as exc:  # provider/network errors -> next in chain
                breaker.failure()
                last_error = exc
                log.warning("llm.provider_failed", model=candidate, error=str(exc)[:200])
                continue
            breaker.success()
            return result
        raise ProviderUnavailableError(f"no provider produced output for {prompt.name}: {last_error}")

    async def _structured_once[T: BaseModel](
        self,
        model: str,
        provider: Provider | None,
        messages: list[dict[str, str]],
        schema: type[T],
        json_schema: dict[str, Any],
        temperature: float,
    ) -> T:
        attempt_messages = list(messages)
        for repair in range(2):  # original call + one repair retry
            response, cached = await self._call(model, provider, attempt_messages, json_schema, temperature)
            try:
                parsed = schema.model_validate(extract_json(response.text))
            except (ValidationError, json.JSONDecodeError, ValueError) as exc:
                if repair == 1:
                    raise StructuredOutputError(f"{schema.__name__} invalid after repair: {exc}") from exc
                attempt_messages = [
                    *attempt_messages,
                    {"role": "assistant", "content": response.text},
                    {
                        "role": "user",
                        "content": "Your reply did not match the required JSON schema. Error: "
                        f"{str(exc)[:800]}\nReturn ONLY a corrected JSON object.",
                    },
                ]
                continue
            if not cached:
                self.cache.put(
                    cache_key(model, attempt_messages, json_schema),
                    {
                        "text": response.text,
                        "prompt_tokens": response.prompt_tokens,
                        "completion_tokens": response.completion_tokens,
                    },
                )
            return parsed
        raise StructuredOutputError("unreachable")  # pragma: no cover

    async def _call(
        self,
        model: str,
        provider: Provider | None,
        messages: list[dict[str, str]],
        json_schema: dict[str, Any],
        temperature: float,
    ) -> tuple[ProviderResponse, bool]:
        key = cache_key(model, messages, json_schema)
        hit = self.cache.get(key)
        meter = current_meter.get()
        if hit is not None:
            resp = ProviderResponse(hit["text"], int(hit["prompt_tokens"]), int(hit["completion_tokens"]))
            if meter:
                meter.record(current_node.get(), model, resp.prompt_tokens, resp.completion_tokens, True)
            return resp, True
        if self.mode == "replay" or provider is None:
            raise ReplayMissError(f"no cached response for {model}")
        request = ProviderRequest(
            model=model, messages=messages, temperature=temperature, timeout_s=self.timeout_s
        )
        delay = self.backoff_base_s
        for attempt in range(self.max_retries + 1):
            try:
                resp = await asyncio.wait_for(provider.complete(request), timeout=self.timeout_s)
                break
            except Exception:
                if attempt == self.max_retries:
                    raise
                await asyncio.sleep(delay)
                delay *= 2
        if meter:
            meter.record(current_node.get(), model, resp.prompt_tokens, resp.completion_tokens, False)
        return resp, False
