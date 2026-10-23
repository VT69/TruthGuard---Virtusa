"""Rerankers. Default: a deterministic lexical cross-scorer that rewards query-term overlap
and registry (subject / attribute) agreement between query and chunk. Optional: a small
CPU cross-encoder (ms-marco-MiniLM) via sentence-transformers."""

from __future__ import annotations

from typing import Protocol

from truthguard.ingestion.lexicon import Lexicon
from truthguard.retrieval.lexical import tokenize


class Reranker(Protocol):
    def score(self, query: str, passages: list[str]) -> list[float]: ...


class LexicalReranker:
    def __init__(self, lexicon: Lexicon) -> None:
        self.lex = lexicon

    def score(self, query: str, passages: list[str]) -> list[float]:
        q = set(tokenize(query))
        q_subj = {m.id for m in self.lex.match_subjects(query)}
        q_attr = {m.id for m in self.lex.match_attributes(query)}
        out = []
        for p in passages:
            toks = set(tokenize(p))
            overlap = len(q & toks) / max(1, len(q))
            subj = {m.id for m in self.lex.match_subjects(p)}
            attr = {m.id for m in self.lex.match_attributes(p)}
            bonus = 0.0
            if q_subj and q_subj & subj:
                bonus += 0.35
            if q_attr and q_attr & attr:
                bonus += 0.5
            out.append(overlap + bonus)
        return out


class CrossEncoderReranker:  # pragma: no cover - optional heavy dependency
    def __init__(self, model: str) -> None:
        from sentence_transformers import CrossEncoder

        self._m = CrossEncoder(model, device="cpu")

    def score(self, query: str, passages: list[str]) -> list[float]:
        return [float(x) for x in self._m.predict([(query, p) for p in passages])]
