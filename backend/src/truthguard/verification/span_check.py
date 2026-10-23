"""Span integrity: a cited quote must exist verbatim in its chunk (whitespace-normalized).
Citations are checked in code, never trusted from a model."""

from __future__ import annotations

import re

_WS = re.compile(r"\s+")


def _normalize_with_map(text: str) -> tuple[str, list[int]]:
    out: list[str] = []
    index: list[int] = []
    prev_space = False
    for i, ch in enumerate(text):
        if ch.isspace():
            if prev_space or not out:
                continue
            out.append(" ")
            index.append(i)
            prev_space = True
        else:
            out.append(ch)
            index.append(i)
            prev_space = False
    return "".join(out), index


def locate_quote(quote: str, text: str) -> tuple[int, int] | None:
    """Return (start, end) offsets of `quote` inside `text`, or None if it is not there."""
    q = _WS.sub(" ", quote).strip()
    if not q:
        return None
    direct = text.find(quote)
    if direct >= 0:
        return direct, direct + len(quote)
    norm, index = _normalize_with_map(text)
    pos = norm.find(q)
    if pos < 0:
        return None
    return index[pos], index[pos + len(q) - 1] + 1


def verify_quote(quote: str, text: str) -> bool:
    return locate_quote(quote, text) is not None
