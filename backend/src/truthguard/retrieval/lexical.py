"""In-process BM25 (Okapi) over chunk index text. Postgres full-text is the documented
alternative (ADR 0005); at demo scale an in-memory index is faster and identical across
SQLite and PostgreSQL."""

from __future__ import annotations

import math
import re
from collections import Counter

_TOKEN = re.compile(r"[a-z0-9]+")
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
        "what",
        "how",
        "does",
        "do",
        "i",
        "my",
        "me",
        "can",
    ]
)


def tokenize(text: str) -> list[str]:
    out = []
    for tok in _TOKEN.findall(text.lower()):
        if tok in _STOP:
            continue
        # crude plural folding so "fees"/"fee" and "deposits"/"deposit" match
        out.append(tok[:-1] if len(tok) > 4 and tok.endswith("s") and not tok.endswith("ss") else tok)
    return out


class BM25Index:
    def __init__(self, k1: float = 1.4, b: float = 0.75) -> None:
        self.k1 = k1
        self.b = b
        self.ids: list[str] = []
        self.tfs: list[Counter[str]] = []
        self.lengths: list[int] = []
        self.df: Counter[str] = Counter()

    def add(self, doc_id: str, text: str) -> None:
        toks = tokenize(text)
        tf = Counter(toks)
        self.ids.append(doc_id)
        self.tfs.append(tf)
        self.lengths.append(len(toks))
        self.df.update(tf.keys())

    def remove(self, ids: set[str]) -> None:
        keep = [i for i, d in enumerate(self.ids) if d not in ids]
        self.ids = [self.ids[i] for i in keep]
        self.tfs = [self.tfs[i] for i in keep]
        self.lengths = [self.lengths[i] for i in keep]
        self.df = Counter()
        for tf in self.tfs:
            self.df.update(tf.keys())

    def search(self, query: str, top_k: int) -> list[tuple[str, float]]:
        if not self.ids:
            return []
        q = tokenize(query)
        n = len(self.ids)
        avgdl = sum(self.lengths) / n
        scores: list[tuple[str, float]] = []
        for i, tf in enumerate(self.tfs):
            s = 0.0
            for t in q:
                f = tf.get(t)
                if not f:
                    continue
                idf = math.log(1 + (n - self.df[t] + 0.5) / (self.df[t] + 0.5))
                s += idf * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * self.lengths[i] / avgdl))
            if s > 0:
                scores.append((self.ids[i], s))
        scores.sort(key=lambda x: (-x[1], x[0]))
        return scores[:top_k]
