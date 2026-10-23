"""Prompt-injection detector. Documents are data: a flagged chunk is quarantined, shown with
a badge in the UI, and its assertions can never win a resolution."""

from __future__ import annotations

import re

STRONG = [
    r"ignore (?:all |any )?(?:the )?(?:previous|prior|above|earlier) (?:instructions|rules|prompts?)",
    r"disregard (?:all |any )?(?:the )?(?:previous|prior|above|system) (?:instructions|rules|prompts?)",
    r"\bsystem (?:note|prompt|message|override)\b.{0,40}\b(?:ai|assistant|llm|model)s?\b",
    r"\b(?:note|message|instruction)s? (?:to|for) (?:ai|llm|the) (?:assistants?|models?|chatbots?)\b",
    r"\byou are now\b",
    r"\bact as (?:an? )?(?:unrestricted|jailbroken)\b",
    r"\bdo not (?:tell|mention|reveal|disclose) (?:this|any other|the user)",
    r"\b(?:new|updated) instructions? for (?:the )?(?:ai|assistant|model)\b",
]
WEAK = [
    r"\btell (?:every|all|the) customers?\b",
    r"\bassistants?\b",
    r"\balways (?:say|answer|respond)\b",
]
_STRONG = [re.compile(p, re.IGNORECASE) for p in STRONG]
_WEAK = [re.compile(p, re.IGNORECASE) for p in WEAK]


def injection_score(text: str) -> float:
    strong = sum(1 for p in _STRONG if p.search(text))
    weak = sum(1 for p in _WEAK if p.search(text))
    return min(1.0, 0.6 * strong + 0.2 * weak)


def is_injection(text: str, threshold: float = 0.6) -> bool:
    return injection_score(text) >= threshold
