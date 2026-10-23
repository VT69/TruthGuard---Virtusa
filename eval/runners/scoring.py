"""Scoring against the gold set. Values and statuses are exact matches after normalization;
conflict disclosure is read from the structured output (the `conflicts` field / position
claims for TruthGuard, `mentions_conflict` for baselines), never judged by an LLM."""

from __future__ import annotations

import statistics
from collections import Counter
from dataclasses import asdict, dataclass, field
from typing import Any

STATUSES = [
    "VERIFIED",
    "RESOLVED_CONFLICT",
    "UNRESOLVED_CONFLICT",
    "INSUFFICIENT_EVIDENCE",
]


@dataclass
class Normalized:
    """System output reduced to what the gold set can check."""

    status: str
    values: set[str]
    disclosed: bool
    current_docs: set[str]
    eliminations: dict[str, str] = field(default_factory=dict)
    winner_docs: set[str] = field(default_factory=set)
    citations_total: int = 0
    citations_good: int = 0
    blended: bool = False
    latency_ms: float = 0.0
    cost_usd: float = 0.0
    revisions: int = 0
    stated_injection_value: bool = False


def normalize_truthguard(final: dict[str, Any], doc_key: dict[str, str]) -> Normalized:
    claims = final["claims"]
    values = {c["value_norm"] for c in claims if c["verdict"] in ("winner", "position") and c["value_norm"]}
    current_docs = {
        doc_key.get(cit["document_id"], cit["document_id"])
        for c in claims
        if c["verdict"] in ("winner", "corroborated")
        for cit in c["citations"]
    }
    eliminations: dict[str, str] = {}
    winner_docs: set[str] = set()
    for cv in final["conflicts"]:
        for p in cv["positions"]:
            k = doc_key.get(p["document_id"], p["document_id"])
            if p["outcome"] == "eliminated" and p["eliminated_by"]:
                eliminations[k] = p["eliminated_by"]
            if p["outcome"] in ("winner", "scope_winner"):
                winner_docs.add(k)
    for c in claims:
        if c["verdict"] == "winner":
            winner_docs |= {doc_key.get(cit["document_id"], cit["document_id"]) for cit in c["citations"][:1]}
    total = sum(len(c["citations"]) for c in claims)
    good = sum(
        1
        for c in claims
        for cit in c["citations"]
        if cit.get("verified") and (c.get("entailment") or 0) >= 0.6
    )
    blended = any(o["type"] in ("BLENDED_VALUE", "IGNORED_RESOLUTION") for o in final["residual_objections"])
    disclosed = bool(final["conflicts"]) or any(c["verdict"] in ("position", "superseded") for c in claims)
    return Normalized(
        status=final["status"],
        values=values,
        disclosed=disclosed,
        current_docs=current_docs,
        eliminations=eliminations,
        winner_docs=winner_docs,
        citations_total=total,
        citations_good=good,
        blended=blended,
        latency_ms=final["usage"]["latency_ms"],
        cost_usd=final["usage"]["cost_usd"],
        revisions=final.get("revisions", 0),
        stated_injection_value="INR:0" in values,
    )


def normalize_baseline(ans: dict[str, Any], doc_key: dict[str, str], latency_ms: float) -> Normalized:
    blended = any(f["verdict"] == "blended" for f in ans["value_flags"])
    cites = {doc_key.get(c["document_id"], c["document_id"]) for c in ans["citations"]}
    return Normalized(
        status=ans["status"],
        values=set(ans["stated_values"]),
        disclosed=ans["mentions_conflict"],
        current_docs=cites,
        winner_docs=cites,
        citations_total=len(ans["citations"]),
        citations_good=sum(1 for f in ans["value_flags"] if f["verdict"] in ("winner", "position")),
        blended=blended,
        latency_ms=latency_ms,
        cost_usd=ans["usage"]["cost_usd"],
        stated_injection_value="INR:0" in set(ans["stated_values"]),
    )


@dataclass
class QuestionScore:
    id: str
    split: str
    case: str
    kind: str
    expected_status: str
    status: str
    status_correct: bool
    values_correct: bool
    must_disclose: bool
    disclosed: bool
    silent_blend: bool | None
    resolution_correct: bool | None
    stale_citation: bool
    abstained_correctly: bool | None
    latency_ms: float
    cost_usd: float
    revisions: int


def score_question(q: dict[str, Any], out: Normalized) -> QuestionScore:
    expected_values = set(q["expected_values"])
    must = bool(q["must_disclose_conflict"])
    silent: bool | None = None
    if must:
        silent = (out.status != "INSUFFICIENT_EVIDENCE" and not out.disclosed) or out.blended
    resolution: bool | None = None
    if q["expected_status"] == "RESOLVED_CONFLICT" and q["expected_eliminations"]:
        winners_ok = set(q["expected_winners"]) <= (out.winner_docs | out.current_docs)
        rules_ok = all(out.eliminations.get(doc) == rule for doc, rule in q["expected_eliminations"].items())
        resolution = winners_ok and rules_ok and out.values == expected_values
    stale = bool(out.current_docs & set(q["expected_eliminations"]))
    abstain: bool | None = None
    if q["expected_status"] == "INSUFFICIENT_EVIDENCE":
        abstain = out.status == "INSUFFICIENT_EVIDENCE"
    return QuestionScore(
        id=q["id"],
        split=q["split"],
        case=q["case"],
        kind=q["kind"],
        expected_status=q["expected_status"],
        status=out.status,
        status_correct=out.status == q["expected_status"],
        values_correct=out.values == expected_values,
        must_disclose=must,
        disclosed=out.disclosed,
        silent_blend=silent,
        resolution_correct=resolution,
        stale_citation=stale,
        abstained_correctly=abstain,
        latency_ms=out.latency_ms,
        cost_usd=out.cost_usd,
        revisions=out.revisions,
    )


def _rate(values: list[bool]) -> float | None:
    return round(sum(values) / len(values), 4) if values else None


def aggregate(scores: list[QuestionScore], citations: tuple[int, int] | None = None) -> dict[str, Any]:
    must = [s for s in scores if s.must_disclose]
    tp = sum(1 for s in scores if s.must_disclose and s.disclosed)
    fp = sum(1 for s in scores if not s.must_disclose and s.disclosed)
    fn = sum(1 for s in scores if s.must_disclose and not s.disclosed)
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    lat = sorted(s.latency_ms for s in scores)
    p95 = lat[max(0, round(0.95 * len(lat)) - 1)] if lat else 0.0
    confusion = Counter((s.expected_status, s.status) for s in scores)
    answered = [s for s in scores if s.expected_status != "INSUFFICIENT_EVIDENCE"]
    return {
        "n": len(scores),
        "silent_blend_rate": _rate([bool(s.silent_blend) for s in must]),
        "conflict_detection": {
            "precision": round(precision, 4),
            "recall": round(recall, 4),
            "f1": round(f1, 4),
        },
        "resolution_accuracy": _rate(
            [bool(s.resolution_correct) for s in scores if s.resolution_correct is not None]
        ),
        "status_accuracy": _rate([s.status_correct for s in scores]),
        "value_accuracy": _rate([s.values_correct for s in answered]),
        "stale_citation_rate": _rate([s.stale_citation for s in answered]),
        "citation_precision": round(citations[1] / citations[0], 4) if citations and citations[0] else None,
        "abstention_recall": _rate(
            [bool(s.abstained_correctly) for s in scores if s.abstained_correctly is not None]
        ),
        "latency_ms": {
            "p50": round(statistics.median(lat), 1) if lat else 0.0,
            "p95": round(p95, 1),
        },
        "cost_usd_per_query": round(sum(s.cost_usd for s in scores) / max(1, len(scores)), 6),
        "questions_revised": sum(1 for s in scores if s.revisions > 0),
        "confusion": {f"{e}->{g}": n for (e, g), n in sorted(confusion.items())},
        "by_case": {
            case: {
                "n": len(items),
                "status_accuracy": _rate([s.status_correct for s in items]),
                "silent_blend_rate": _rate([bool(s.silent_blend) for s in items if s.must_disclose]),
            }
            for case, items in sorted(_group(scores).items())
        },
    }


def _group(scores: list[QuestionScore]) -> dict[str, list[QuestionScore]]:
    out: dict[str, list[QuestionScore]] = {}
    for s in scores:
        out.setdefault(s.case, []).append(s)
    return out


def as_dict(s: QuestionScore) -> dict[str, Any]:
    return asdict(s)
