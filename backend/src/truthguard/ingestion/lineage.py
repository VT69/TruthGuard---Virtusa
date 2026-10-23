"""Lineage extraction: find "supersedes / replaces / amends clause 4.2 / supplements"
statements, then match their targets against the documents in the corpus.

Extraction runs per document at ingest; matching runs corpus-wide after every ingest so a
statement whose target arrives later still resolves (ingest order does not matter).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from pydantic import BaseModel

from truthguard.core.ids import stable_id
from truthguard.domain.enums import Provenance, Relation
from truthguard.domain.models import Document, LineageEdge
from truthguard.ingestion.metadata import LONG_DATE, parse_date, series_key


class LineageStatement(BaseModel):
    relation: Relation
    target_text: str
    clause: str | None = None
    evidence_span: str
    provenance: Provenance = Provenance.INFERRED
    confidence: float = 0.9


_DOC_NOUN = r"(?:policy|circular|notice|procedure|document|addendum|amendment|schedule|manual|faq|terms)"
PATTERNS: list[tuple[re.Pattern[str], Relation]] = [
    (
        re.compile(
            r"In partial modification of (?:the )?(?P<target>[^,.]+?),\s*clause (?P<clause>\d+(?:\.\d+)*)\s+is (?:hereby )?amended",
            re.I,
        ),
        Relation.AMENDS,
    ),
    (
        re.compile(
            r"\b(?:hereby )?amends? clause (?P<clause>\d+(?:\.\d+)*) of (?:the )?(?P<target>[^,.;]+?)(?:\s+with effect from|[.;,]|$)",
            re.I,
        ),
        Relation.AMENDS,
    ),
    (
        re.compile(
            rf"\bThis {_DOC_NOUN} (?:supersedes|replaces) (?:the )?(?P<target>.+?)(?:\s+with effect from|\s+in respect of|\s+for\s|[;]|\.(?:\s|$)|$)",
            re.I,
        ),
        Relation.SUPERSEDES,
    ),
    (
        re.compile(
            r"\b(?:supersedes|replaces|is in supersession of) (?:the )?(?P<target>[A-Z][^.;]+?)(?:\s+with effect from|\s+in respect of|[;]|\.(?:\s|$)|$)"
        ),
        Relation.SUPERSEDES,
    ),
    (
        re.compile(
            rf"\bThis {_DOC_NOUN} (?:supplements|clarifies) (?:the )?(?P<target>.+?)(?:\s+for\s|[;]|\.(?:\s|$)|$)",
            re.I,
        ),
        Relation.CLARIFIES,
    ),
    (
        re.compile(
            rf"\b(?:refer to|in accordance with) (?:the )?(?P<target>[A-Z][\w -]+? {_DOC_NOUN}(?: v\d+| \d{{4}})?)",
            re.I,
        ),
        Relation.REFERENCES,
    ),
]


def extract_statements(raw_text: str, supersedes_hint: list[str] | None = None) -> list[LineageStatement]:
    out: list[LineageStatement] = []
    seen: set[tuple[str, str]] = set()
    for hint in supersedes_hint or []:
        out.append(
            LineageStatement(
                relation=Relation.SUPERSEDES,
                target_text=hint,
                evidence_span=f"supersedes: {hint}",
                provenance=Provenance.EXPLICIT,
                confidence=1.0,
            )
        )
        seen.add((Relation.SUPERSEDES.value, hint.lower()))
    for line in raw_text.splitlines():
        for pattern, relation in PATTERNS:
            for m in pattern.finditer(line):
                target = m.group("target").strip().rstrip(".")
                key = (relation.value, target.lower())
                if key in seen or len(target) < 4:
                    continue
                seen.add(key)
                clause = m.groupdict().get("clause")
                out.append(
                    LineageStatement(
                        relation=relation,
                        target_text=target,
                        clause=clause,
                        evidence_span=line.strip(),
                        confidence=0.9,
                    )
                )
    return out


@dataclass(frozen=True)
class TargetMatch:
    doc_id: str
    score: float


def _tokens(text: str) -> set[str]:
    stop = {"the", "of", "and", "a", "an", "dated", "to", "no", "for"}
    return {t for t in re.findall(r"[a-z0-9]+", text.lower()) if t not in stop}


def match_target(target_text: str, docs: list[Document], exclude: str) -> TargetMatch | None:
    """Match "Card Fees and Charges Policy v1" / "...Circular dated 1 January 2025" to a document."""
    version = re.search(r"\bv(\d+)\b", target_text, re.I)
    year = re.search(r"\b(20\d{2})\b", target_text)
    dated = re.search(LONG_DATE, target_text, re.I)
    target_date = parse_date(dated.group(1)) if dated else None
    target_key = _tokens(series_key(target_text).replace("_", " "))
    best: TargetMatch | None = None
    for d in docs:
        if d.id == exclude:
            continue
        doc_key = _tokens(series_key(d.title).replace("_", " "))
        if not doc_key or not target_key:
            continue
        overlap = len(doc_key & target_key) / len(doc_key | target_key)
        if overlap < 0.5:
            continue
        score = overlap
        if version:
            score += 0.5 if (d.version_label or "").lower() == f"v{version.group(1)}" else -0.5
        if target_date:
            score += 0.5 if target_date in (d.effective_from, d.published_at) else -0.5
        elif year:
            years = {str(x.year) for x in (d.effective_from, d.published_at) if x}
            years |= set(re.findall(r"20\d{2}", f"{d.title} {d.version_label or ''}"))
            score += 0.3 if year.group(1) in years else -0.4
        if best is None or score > best.score:
            best = TargetMatch(d.id, score)
    if best is None or best.score < 0.6:
        return None
    return best


def resolve_edges(statements: dict[str, list[LineageStatement]], docs: list[Document]) -> list[LineageEdge]:
    by_id = {d.id: d for d in docs}
    edges: list[LineageEdge] = []
    for doc_id, stmts in statements.items():
        source = by_id.get(doc_id)
        if source is None:
            continue
        date_meta = [source.field_provenance.get(k) for k in ("effective_from", "published")]
        # an undated or weakly dated source document makes its own lineage claims less trustworthy
        inferred_meta = not any(m is not None and m.confidence >= 0.7 for m in date_meta)
        for st in stmts:
            target = by_id.get(st.target_text) or None
            score = 1.0
            if target is None:
                tm = match_target(st.target_text, docs, exclude=doc_id)
                if tm is None:
                    continue
                target = by_id[tm.doc_id]
                score = min(1.0, tm.score)
            confidence = st.confidence * score
            provenance = st.provenance
            if provenance != Provenance.EXPLICIT and inferred_meta:
                confidence *= 0.8  # undated / metadata-poor source: lower trust in the edge
            edges.append(
                LineageEdge(
                    id=stable_id("edge", doc_id, target.id, st.relation.value, st.clause or ""),
                    from_doc=doc_id,
                    to_doc=target.id,
                    relation=st.relation,
                    scope_section=st.clause,
                    evidence_span=st.evidence_span,
                    provenance=provenance,
                    confidence=round(confidence, 3),
                )
            )
    # de-duplicate (same from/to/relation/scope), keeping the most confident
    unique: dict[tuple[str, str, str, str | None], LineageEdge] = {}
    for e in edges:
        k = (e.from_doc, e.to_doc, e.relation.value, e.scope_section)
        if k not in unique or e.confidence > unique[k].confidence:
            unique[k] = e
    return list(unique.values())


def assign_series(
    docs: list[Document], edges: list[LineageEdge], hints: dict[str, str | None]
) -> dict[str, str]:
    """Union-find: explicit series hints, equal title keys, and supersedes/amends edges."""
    parent: dict[str, str] = {d.id: d.id for d in docs}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)

    by_key: dict[str, str] = {}
    for d in docs:
        key = f"hint:{hints[d.id]}" if hints.get(d.id) else f"title:{series_key(d.title)}"
        if key in by_key:
            union(by_key[key], d.id)
        else:
            by_key[key] = d.id
    for e in edges:
        if e.relation.is_superseding and e.from_doc in parent and e.to_doc in parent:
            union(e.from_doc, e.to_doc)
    names: dict[str, str] = {}
    for d in docs:
        root = find(d.id)
        if root not in names:
            members = [x for x in docs if find(x.id) == root]
            hint = next((hints[m.id] for m in members if hints.get(m.id)), None)
            names[root] = hint or series_key(min(members, key=lambda m: m.id).title)
    return {d.id: names[find(d.id)] for d in docs}


def section_matches(scope_section: str | None, section_path: list[str]) -> bool:
    """Does an edge scoped to clause "4.2" cover an assertion found under this heading path?"""
    if not scope_section:
        return True
    for heading in section_path:
        m = re.match(r"^\s*(?:clause\s+|section\s+|§\s*)?(\d+(?:\.\d+)*)\b", heading, re.I)
        if m and (m.group(1) == scope_section or m.group(1).startswith(scope_section + ".")):
            return True
        if re.search(rf"\bclause {re.escape(scope_section)}\b", heading, re.I):
            return True
    return False
