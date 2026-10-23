"""Embedders behind one protocol, swappable by EMBED_BACKEND without code changes.

Default `hashing`: deterministic feature-hashed word uni/bi-grams + character trigrams,
L2-normalised. No model download, CPU-trivial, reproducible - ideal for CI, eval replay
and demo safety. `sentence-transformers` (bge-small, 384-d) and any OpenAI-compatible
embeddings endpoint are drop-in upgrades.
"""

from __future__ import annotations

import hashlib
import itertools
import re
from typing import Protocol

import httpx
import numpy as np

_TOKEN = re.compile(r"[a-z0-9₹%]+")
_STOP = frozenset(
    [
        "a",
        "an",
        "the",
        "of",
        "to",
        "in",
        "on",
        "for",
        "and",
        "or",
        "is",
        "are",
        "be",
        "by",
        "with",
        "at",
        "as",
        "it",
        "this",
        "that",
        "from",
        "per",
        "any",
        "all",
        "what",
        "how",
        "does",
        "do",
        "i",
        "my",
        "me",
        "we",
        "you",
        "your",
        "can",
    ]
)


class Embedder(Protocol):
    dim: int

    def embed(self, texts: list[str]) -> np.ndarray: ...


def _h(feature: str, dim: int) -> tuple[int, float]:
    digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
    value = int.from_bytes(digest, "little")
    return value % dim, (1.0 if (value >> 63) & 1 else -1.0)


class HashingEmbedder:
    def __init__(self, dim: int = 384) -> None:
        self.dim = dim

    def _features(self, text: str) -> list[tuple[str, float]]:
        tokens = [t for t in _TOKEN.findall(text.lower()) if t not in _STOP]
        feats: list[tuple[str, float]] = [(f"w:{t}", 1.0) for t in tokens]
        feats += [(f"b:{a}_{b}", 0.7) for a, b in itertools.pairwise(tokens)]
        for t in tokens:
            padded = f"#{t}#"
            feats += [(f"c:{padded[i : i + 3]}", 0.25) for i in range(len(padded) - 2)]
        return feats

    def embed(self, texts: list[str]) -> np.ndarray:
        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        for row, text in enumerate(texts):
            for feat, weight in self._features(text):
                idx, sign = _h(feat, self.dim)
                out[row, idx] += sign * weight
            norm = float(np.linalg.norm(out[row]))
            if norm > 0:
                out[row] /= norm
        return out


class SentenceTransformerEmbedder:  # pragma: no cover - optional heavy dependency
    def __init__(self, model: str) -> None:
        from sentence_transformers import SentenceTransformer

        self._model = SentenceTransformer(model, device="cpu")
        self.dim = int(self._model.get_sentence_embedding_dimension())

    def embed(self, texts: list[str]) -> np.ndarray:
        vecs = self._model.encode(texts, batch_size=32, normalize_embeddings=True, show_progress_bar=False)
        return np.asarray(vecs, dtype=np.float32)


class OpenAICompatEmbedder:  # pragma: no cover - needs a live endpoint
    def __init__(self, base_url: str, api_key: str | None, model: str, dim: int) -> None:
        self._url = base_url.rstrip("/") + "/embeddings"
        self._key = api_key
        self._model = model
        self.dim = dim

    def embed(self, texts: list[str]) -> np.ndarray:
        headers = {"Authorization": f"Bearer {self._key}"} if self._key else {}
        resp = httpx.post(self._url, json={"model": self._model, "input": texts}, headers=headers, timeout=60)
        resp.raise_for_status()
        vecs = np.asarray([d["embedding"] for d in resp.json()["data"]], dtype=np.float32)
        out: np.ndarray = vecs / np.maximum(np.linalg.norm(vecs, axis=1, keepdims=True), 1e-9)
        return out


def build_embedder(
    backend: str, model: str, dim: int, base_url: str = "", api_key: str | None = None
) -> Embedder:
    if backend == "sentence-transformers":
        return SentenceTransformerEmbedder(model)
    if backend == "openai":
        return OpenAICompatEmbedder(base_url or "https://api.openai.com/v1", api_key, model, dim)
    return HashingEmbedder(dim)
