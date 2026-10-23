"""File-backed LLM response cache keyed by model + prompt hash (dev, eval and DEMO_MODE replay)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from truthguard.core.ids import sha256_hex


def cache_key(model: str, messages: list[dict[str, str]], schema: dict[str, Any] | None) -> str:
    payload = json.dumps({"m": model, "msgs": messages, "s": schema}, sort_keys=True, ensure_ascii=False)
    return sha256_hex(payload)


class ResponseCache:
    def __init__(self, directory: Path | None) -> None:
        self._dir = directory
        self._mem: dict[str, dict[str, Any]] = {}
        if directory is not None:
            directory.mkdir(parents=True, exist_ok=True)

    def get(self, key: str) -> dict[str, Any] | None:
        if key in self._mem:
            return self._mem[key]
        if self._dir is None:
            return None
        path = self._dir / f"{key}.json"
        if not path.exists():
            return None
        data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        self._mem[key] = data
        return data

    def put(self, key: str, value: dict[str, Any]) -> None:
        self._mem[key] = value
        if self._dir is not None:
            (self._dir / f"{key}.json").write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
