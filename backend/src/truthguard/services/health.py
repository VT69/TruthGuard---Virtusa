"""Corpus Health: every contradiction in the knowledge base before a user hits it, plus
stale documents still live and "retire this FAQ" suggestions. Also the lineage timeline."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date
from typing import Any

from truthguard.domain.enums import DocStatus, ResolutionStatus, RuleId
from truthguard.domain.models import ConflictSet, Resolution
from truthguard.ingestion.lexicon import Lexicon
from truthguard.ingestion.normalize import display_for_norm
from truthguard.retrieval.corpus import CorpusSnapshot

SEVERITY_RANK = {"minor": 1, "major": 2, "critical": 3}


def conflict_rows(
    conflicts: list[tuple[ConflictSet, Resolution, str]], corpus: CorpusSnapshot, lex: Lexicon
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for c, r, severity in conflicts:
        positions = []
        for aid in c.assertion_ids:
            a = corpus.assertions.get(aid)
            if a is None:
                continue
            d = corpus.documents[a.document_id]
            when = d.effective_from or d.published_at
            rule = r.eliminated_by.get(aid)
            outcome = (
                "unresolved"
                if r.status == ResolutionStatus.UNRESOLVED and rule is None
                else "winner"
                if aid in r.winner_assertion_ids
                else "eliminated"
                if rule
                else "agrees"
            )
            positions.append(
                {
                    "assertion_id": aid,
                    "document_id": d.id,
                    "title": d.title,
                    "version": d.version_label,
                    "doc_type": d.doc_type,
                    "tier": d.authority_tier,
                    "date": when.isoformat() if when else None,
                    "value": a.value_display,
                    "scope": a.scope,
                    "quote": a.quote,
                    "chunk_id": a.chunk_id,
                    "outcome": outcome,
                    "eliminated_by": rule.value if rule else None,
                }
            )
        rows.append(
            {
                "id": c.id,
                "slot_key": c.slot_key,
                "label": lex.slot_label(c.slot_key),
                "topic": c.topic or "general",
                "type": c.conflict_type.value,
                "secondary_types": [t.value for t in c.secondary_types],
                "severity": severity,
                "values": c.distinct_values,
                "status": r.status.value,
                "deciding_rule": r.deciding_rule.value if r.deciding_rule else None,
                "flags": r.flags,
                "rationale": r.rationale,
                "rule_chain": [e.model_dump(mode="json") for e in r.rule_chain],
                "positions": positions,
                "owner": lex.topic_owners.get(c.topic or "general"),
            }
        )
    rows.sort(key=lambda x: (-SEVERITY_RANK.get(x["severity"], 0), x["topic"], x["slot_key"]))
    return rows


def health_summary(
    conflicts: list[tuple[ConflictSet, Resolution, str]], corpus: CorpusSnapshot, lex: Lexicon, as_of: date
) -> dict[str, Any]:
    rows = conflict_rows(conflicts, corpus, lex)
    heat: dict[tuple[str, str], str] = {}
    for row in rows:
        for p in row["positions"]:
            key = (row["topic"], p["document_id"])
            if SEVERITY_RANK[row["severity"]] > SEVERITY_RANK.get(heat.get(key, ""), 0):
                heat[key] = row["severity"]
    topics = sorted({r["topic"] for r in rows} | {d.topic or "general" for d in corpus.documents.values()})
    docs_by_topic: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for d in sorted(corpus.documents.values(), key=lambda d: (d.topic or "general", d.title)):
        docs_by_topic[d.topic or "general"].append(
            {"id": d.id, "title": d.title, "version": d.version_label, "doc_type": d.doc_type}
        )
    cells = [{"topic": t, "document_id": doc, "severity": sev} for (t, doc), sev in sorted(heat.items())]

    # stale documents still live + retire suggestions
    losing: dict[str, list[tuple[str, RuleId]]] = defaultdict(list)
    for c, r, _ in conflicts:
        for aid, rule in r.eliminated_by.items():
            a = corpus.assertions.get(aid)
            if a and rule in (RuleId.R1, RuleId.R2, RuleId.R4):
                losing[a.document_id].append((c.slot_key, rule))
    stale_docs = []
    suggestions: list[dict[str, Any]] = []
    for doc_id, losses in sorted(losing.items()):
        d = corpus.documents[doc_id]
        status = corpus.temporal_status(doc_id, as_of)
        slots = sorted({lex.slot_label(s) for s, _ in losses})
        rules = sorted({r.value for _, r in losses})
        stale_docs.append(
            {
                "document_id": doc_id,
                "title": d.title,
                "version": d.version_label,
                "doc_type": d.doc_type,
                "status": status.value,
                "losing_slots": slots,
                "rules": rules,
            }
        )
        if d.doc_type in ("faq", "help_article"):
            suggestions.append(
                {
                    "document_id": doc_id,
                    "action": "retire_or_update",
                    "text": f"Retire or update “{d.title}”: it contradicts current policy on "
                    f"{', '.join(slots)}.",
                }
            )
        elif status == DocStatus.SUPERSEDED:
            suggestions.append(
                {
                    "document_id": doc_id,
                    "action": "archive",
                    "text": f"Archive “{d.label()}”: it has been superseded but is still searchable.",
                }
            )
    for row in rows:
        if row["status"] == "unresolved":
            suggestions.append(
                {
                    "document_id": None,
                    "action": "escalate",
                    "text": f"Ask the {row['owner']} to settle “{row['label']}” "
                    f"({' vs '.join(display_for_norm(v) for v in row['values'])}).",
                }
            )
    injected = [
        {"document_id": d.id, "title": d.title} for d in corpus.documents.values() if d.injection_flag
    ]
    return {
        "as_of": as_of.isoformat(),
        "totals": {
            "conflicts": len(rows),
            "unresolved": sum(1 for r in rows if r["status"] == "unresolved"),
            "by_severity": dict(Counter(r["severity"] for r in rows)),
            "by_type": dict(Counter(r["type"] for r in rows)),
            "by_topic": dict(Counter(r["topic"] for r in rows)),
            "documents": len(corpus.documents),
            "stale_documents": len(stale_docs),
            "quarantined_documents": len(injected),
        },
        "conflicts": rows,
        "heatmap": {"topics": topics, "documents": dict(docs_by_topic), "cells": cells},
        "stale_documents": stale_docs,
        "suggestions": suggestions,
        "quarantined": injected,
    }


def report_markdown(summary: dict[str, Any]) -> str:
    t = summary["totals"]
    lines = [
        "# TruthGuard Corpus Health Report",
        "",
        f"As of {summary['as_of']}: **{t['conflicts']} contradictions** across {t['documents']} documents "
        f"({t['unresolved']} unresolved, {t['stale_documents']} stale documents still live, "
        f"{t['quarantined_documents']} quarantined).",
        "",
        "| Severity | Topic | Slot | Values | Status | Rule |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for r in summary["conflicts"]:
        lines.append(
            f"| {r['severity']} | {r['topic']} | {r['label']} | "
            f"{' / '.join(display_for_norm(v) for v in r['values'])} | {r['status']} | "
            f"{r['deciding_rule'] or '-'} |"
        )
    lines += ["", "## Recommended actions", ""]
    lines += [f"- {s['text']}" for s in summary["suggestions"]]
    return "\n".join(lines) + "\n"


def series_title(titles: list[str]) -> str:
    """The title most members share (ties: the shortest), e.g. 'Card Fees and Charges Policy'."""
    counts = Counter(titles)
    return min(counts, key=lambda t: (-counts[t], len(t), t))


def lineage_view(corpus: CorpusSnapshot, as_of: date, series_id: str | None = None) -> list[dict[str, Any]]:
    by_series: dict[str, list[str]] = defaultdict(list)
    for d in corpus.documents.values():
        by_series[d.series_id].append(d.id)
    out = []
    for sid, ids in sorted(by_series.items()):
        if series_id and sid != series_id:
            continue
        edges = [e for e in corpus.edges if e.from_doc in ids or e.to_doc in ids]
        if len(ids) < 2 and not edges and not series_id:
            continue
        related = set(ids) | {e.from_doc for e in edges} | {e.to_doc for e in edges}
        docs = []
        for did in sorted(related, key=lambda x: (corpus.documents[x].sort_date or date.min, x)):
            d = corpus.documents[did]
            docs.append(
                {
                    "id": d.id,
                    "title": d.title,
                    "version": d.version_label,
                    "doc_type": d.doc_type,
                    "tier": d.authority_tier,
                    "effective_from": d.effective_from.isoformat() if d.effective_from else None,
                    "published_at": d.published_at.isoformat() if d.published_at else None,
                    "status": corpus.temporal_status(did, as_of).value,
                    "in_series": did in ids,
                    "inferred_dates": not any(
                        m.provenance.value == "explicit"
                        for k, m in d.field_provenance.items()
                        if k in ("effective_from", "published")
                    ),
                }
            )
        out.append(
            {
                "series_id": sid,
                "title": series_title([corpus.documents[i].title for i in ids]),
                "topic": corpus.documents[ids[0]].topic,
                "documents": docs,
                "edges": [e.model_dump(mode="json") for e in edges],
            }
        )
    return out
