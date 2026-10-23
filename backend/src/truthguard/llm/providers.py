"""LLM providers. Model ids are `provider/model`, e.g. `groq/llama-3.3-70b-versatile`.

Every hosted provider we target (Groq, Gemini, OpenAI, Anthropic, Ollama) exposes an
OpenAI-compatible chat-completions endpoint, so one small httpx client covers them all
without a heavy SDK. `FakeProvider` serves tests and CI without any API key.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

DEFAULT_BASE_URLS: dict[str, str] = {
    "groq": "https://api.groq.com/openai/v1",
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai",
    "openai": "https://api.openai.com/v1",
    "anthropic": "https://api.anthropic.com/v1",
}

# Model used when the gateway falls back to a provider other than the configured one.
FALLBACK_MODELS: dict[str, str] = {
    "groq": "groq/llama-3.3-70b-versatile",
    "gemini": "gemini/gemini-2.0-flash",
    "openai": "openai/gpt-4o-mini",
    "anthropic": "anthropic/claude-haiku-4-5",
    "ollama": "ollama/qwen2.5:7b",
}


@dataclass(frozen=True)
class ProviderRequest:
    model: str
    messages: list[dict[str, str]]
    temperature: float
    json_mode: bool = True
    timeout_s: float = 30.0


@dataclass(frozen=True)
class ProviderResponse:
    text: str
    prompt_tokens: int
    completion_tokens: int


class Provider(Protocol):
    name: str

    async def complete(self, request: ProviderRequest) -> ProviderResponse: ...


def split_model(model: str) -> tuple[str, str]:
    provider, _, name = model.partition("/")
    if not name:
        raise ValueError(f"model id must look like 'provider/model', got {model!r}")
    return provider, name


class OpenAICompatProvider:
    def __init__(
        self,
        name: str,
        base_url: str,
        api_key: str | None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.name = name
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._transport = transport

    async def complete(self, request: ProviderRequest) -> ProviderResponse:
        _, model_name = split_model(request.model)
        body: dict[str, Any] = {
            "model": model_name,
            "messages": request.messages,
            "temperature": request.temperature,
        }
        if request.json_mode:
            body["response_format"] = {"type": "json_object"}
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        async with httpx.AsyncClient(timeout=request.timeout_s, transport=self._transport) as client:
            resp = await client.post(f"{self._base_url}/chat/completions", json=body, headers=headers)
            resp.raise_for_status()
            data = resp.json()
        text = data["choices"][0]["message"]["content"] or ""
        usage = data.get("usage") or {}
        return ProviderResponse(
            text=text,
            prompt_tokens=int(usage.get("prompt_tokens", 0)),
            completion_tokens=int(usage.get("completion_tokens", 0)),
        )


@dataclass
class FakeProvider:
    """Deterministic provider for tests: a handler or a queue of scripted replies."""

    name: str = "fake"
    handler: Callable[[ProviderRequest], str] | None = None
    script: list[str | Exception] = field(default_factory=list)
    calls: list[ProviderRequest] = field(default_factory=list)

    async def complete(self, request: ProviderRequest) -> ProviderResponse:
        self.calls.append(request)
        if self.script:
            item = self.script.pop(0)
            if isinstance(item, Exception):
                raise item
            text = item
        elif self.handler is not None:
            text = self.handler(request)
        else:
            text = "{}"
        prompt_tokens = sum(len(m["content"]) for m in request.messages) // 4
        return ProviderResponse(text=text, prompt_tokens=prompt_tokens, completion_tokens=len(text) // 4)


def build_providers(
    *,
    groq_api_key: str | None,
    gemini_api_key: str | None,
    openai_api_key: str | None,
    anthropic_api_key: str | None,
    ollama_base_url: str,
) -> dict[str, Provider]:
    providers: dict[str, Provider] = {}
    keys = {
        "groq": groq_api_key,
        "gemini": gemini_api_key,
        "openai": openai_api_key,
        "anthropic": anthropic_api_key,
    }
    for name, key in keys.items():
        if key:
            providers[name] = OpenAICompatProvider(name, DEFAULT_BASE_URLS[name], key)
    providers["ollama"] = OpenAICompatProvider("ollama", f"{ollama_base_url.rstrip('/')}/v1", None)
    return providers
