"""Document metadata: explicit sources first, then inference; every field keeps provenance.

Order of precedence per field (first hit wins, inferred never outranks explicit):
  1. frontmatter / DOCX core properties / HTML meta tags           explicit, 1.0
  2. filename patterns such as `card_fees_v2_2024-03.docx`          explicit, 0.8
  3. header lines in the text ("Effective from: 1 April 2024")      inferred, 0.9
  4. title keywords and body phrases ("with effect from ...")       inferred, 0.6-0.7
  5. LLM inference on the first page (live mode only)               llm, 0.6
"""

from __future__ import annotations

import re
from datetime import date, datetime
from pathlib import Path
from typing import Any

from truthguard.core.ids import slugify
from truthguard.domain.enums import Provenance
from truthguard.domain.models import DocumentMetadata, FieldMeta, ParsedDocument

DOC_TYPES = {
    "board_policy", "policy", "circular", "procedure", "amendment", "addendum", "product_terms",
    "schedule", "notice", "faq", "help_article", "memo", "email", "regulatory",
}  # fmt: skip

TITLE_TYPE_RULES: list[tuple[str, str]] = [
    (r"\bboard[- ]approved\b", "board_policy"),
    (r"\bamendment\b", "amendment"),
    (r"\baddendum\b", "addendum"),
    (r"\bfaq\b|frequently asked", "faq"),
    (r"\bcircular\b", "circular"),
    (r"\bnotice\b", "notice"),
    (r"\bmemo\b", "memo"),
    (r"^email\b|^e-mail\b", "email"),
    (r"\bschedule of charges\b", "schedule"),
    (r"\bterms\b|\bproduct sheet\b", "product_terms"),
    (r"\bprocedure\b|\bmanual\b", "procedure"),
    (r"\bpolicy\b|\bcharter\b", "policy"),
    (r"\bglossary\b|\bhelp\b|\boffers?\b", "help_article"),
]

LABEL_TO_TYPE = {
    "board policy": "board_policy", "policy": "policy", "circular": "circular", "procedure": "procedure",
    "amendment": "amendment", "addendum": "addendum", "product terms": "product_terms",
    "schedule of charges": "schedule", "notice": "notice", "faq": "faq", "help article": "help_article",
    "memo": "memo", "email": "email",
}  # fmt: skip

MONTHS = "january|february|march|april|may|june|july|august|september|october|november|december"
LONG_DATE = rf"(\d{{1,2}} (?:{MONTHS}) \d{{4}})"


def parse_date(value: Any) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    s = str(value).strip()
    for fmt in ("%Y-%m-%d", "%d %B %Y", "%d %b %Y", "%Y/%m/%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    m = re.match(r"^(\d{4})-(\d{2})$", s)
    if m:
        return date(int(m.group(1)), int(m.group(2)), 1)
    return None


class _Fields:
    def __init__(self) -> None:
        self.values: dict[str, Any] = {}
        self.meta: dict[str, FieldMeta] = {}

    def set(self, name: str, value: Any, provenance: Provenance, confidence: float) -> None:
        if value in (None, "", []) or name in self.values:
            return
        self.values[name] = value
        self.meta[name] = FieldMeta(provenance=provenance, confidence=confidence)


def _explicit_map(parsed: ParsedDocument) -> dict[str, Any]:
    """Normalize frontmatter, DOCX core properties and HTML meta tags to one key set."""
    fm = parsed.frontmatter
    props = parsed.properties
    out: dict[str, Any] = {}
    if fm:
        out.update(
            doc_key=fm.get("doc_id"),
            title=fm.get("title"),
            doc_type=fm.get("doc_type"),
            version=fm.get("version"),
            effective_from=fm.get("effective_from"),
            effective_to=fm.get("effective_to"),
            published=fm.get("published"),
            topic=fm.get("topic"),
            series=fm.get("series"),
            issuer=fm.get("issuer"),
            supersedes=fm.get("supersedes"),
            subjects=fm.get("subjects"),
        )
    if parsed.fmt == "docx" and props:
        comments = dict(kv.split("=", 1) for kv in str(props.get("comments", "")).split(";") if "=" in kv)
        comments = {k.strip(): v.strip() for k, v in comments.items()}
        subject = str(props.get("subject", "")).strip()
        out.update(
            doc_key=props.get("identifier"),
            title=props.get("title"),
            doc_type=subject if subject in DOC_TYPES else None,
            version=props.get("version"),
            effective_from=comments.get("effective_from"),
            effective_to=comments.get("effective_to"),
            published=comments.get("published"),
            topic=props.get("category"),
            series=props.get("keywords"),
            issuer=props.get("author"),
        )
    if parsed.fmt == "html" and props:
        out.update(
            doc_key=props.get("doc_id"),
            title=props.get("title"),
            doc_type=props.get("doc_type"),
            version=props.get("version"),
            effective_from=props.get("effective_from"),
            published=props.get("published"),
            topic=props.get("topic"),
            series=props.get("series"),
            issuer=props.get("issuer"),
        )
    return {k: v for k, v in out.items() if v not in (None, "")}


def infer_doc_type(title: str) -> str | None:
    t = title.lower()
    for pattern, doc_type in TITLE_TYPE_RULES:
        if re.search(pattern, t):
            return doc_type
    return None


def series_key(title: str) -> str:
    """Title with version, date and edition words stripped: groups versions of one document."""
    t = title.lower()
    t = re.sub(rf"\b\d{{1,2}} (?:{MONTHS}) \d{{4}}\b", " ", t)
    t = re.sub(r"\(.*?(\d{4}|v\d+|revised|updated).*?\)", " ", t)
    t = re.sub(r"\b(v\d+(\.\d+)?|version \S+|\d{4}(-\d{2})?|revised|updated|amended|draft|final)\b", " ", t)
    return slugify(t)


def extract_metadata(
    parsed: ParsedDocument,
    filename: str,
    tiers: dict[str, int],
    default_tier: int = 4,
) -> DocumentMetadata:
    f = _Fields()
    explicit = _explicit_map(parsed)
    for key in ("doc_key", "title", "doc_type", "version", "topic", "series", "issuer"):
        if key in explicit:
            f.set(key, str(explicit[key]), Provenance.EXPLICIT, 1.0)
    for key in ("effective_from", "effective_to", "published"):
        d = parse_date(explicit.get(key))
        if d:
            f.set(key, d, Provenance.EXPLICIT, 1.0)
    supersedes = explicit.get("supersedes")
    subjects = explicit.get("subjects")

    # 2. filename patterns
    stem = Path(filename).stem
    m = re.search(r"(?:^|_)v(\d+)(?:_|$)", stem)
    if m:
        f.set("version", f"v{m.group(1)}", Provenance.EXPLICIT, 0.8)
    m = re.search(r"(\d{4})-(\d{2})(?:-(\d{2}))?", stem)
    if m:
        d = date(int(m.group(1)), int(m.group(2)), int(m.group(3) or 1))
        f.set("effective_from", d, Provenance.EXPLICIT, 0.6)

    # 3. header lines in the text
    head = parsed.raw_text[:1500]
    for label, field in (
        (r"effective (?:from|date)", "effective_from"),
        (r"issued on|published|date", "published"),
        (r"valid until|expires on", "effective_to"),
    ):
        m = re.search(rf"^(?:{label}):\s*{LONG_DATE}", head, re.IGNORECASE | re.MULTILINE)
        if m:
            f.set(field, parse_date(m.group(1)), Provenance.INFERRED, 0.9)
    m = re.search(r"^Document type:\s*([A-Za-z ]+?)\s*(?:\||$)", head, re.MULTILINE)
    if m and m.group(1).strip().lower() in LABEL_TO_TYPE:
        f.set("doc_type", LABEL_TO_TYPE[m.group(1).strip().lower()], Provenance.INFERRED, 0.9)
    m = re.search(r"Version:\s*([\w.\-]+)", head)
    if m:
        f.set("version", m.group(1), Provenance.INFERRED, 0.9)
    m = re.search(r"^(?:Issued by|From):\s*(.+)$", head, re.MULTILINE)
    if m:
        f.set("issuer", m.group(1).strip(), Provenance.INFERRED, 0.9)

    # 4. title and body phrases
    title = f.values.get("title") or (
        parsed.sections[0].heading_path[0] if parsed.sections and parsed.sections[0].heading_path else stem
    )
    f.set("title", title, Provenance.INFERRED, 0.7)
    title_for_type = (
        parsed.sections[0].heading_path[0] if parsed.sections and parsed.sections[0].heading_path else title
    )
    inferred_type = infer_doc_type(title_for_type) or infer_doc_type(str(f.values["title"]))
    f.set("doc_type", inferred_type, Provenance.INFERRED, 0.7)
    m = re.search(rf"with effect from {LONG_DATE}", parsed.raw_text, re.IGNORECASE)
    if m:
        f.set("effective_from", parse_date(m.group(1)), Provenance.INFERRED, 0.6)
    m = re.search(r"\((v\d+|\d{4})\)", title_for_type)
    if m:
        f.set("version", m.group(1), Provenance.INFERRED, 0.6)

    doc_type = str(f.values.get("doc_type") or "help_article")
    if "doc_type" not in f.values:
        f.set("doc_type", doc_type, Provenance.INFERRED, 0.3)
    tier = tiers.get(doc_type, default_tier)
    return DocumentMetadata(
        doc_key=f.values.get("doc_key"),
        title=str(f.values["title"]),
        doc_type=doc_type,
        authority_tier=tier,
        version_label=f.values.get("version"),
        effective_from=f.values.get("effective_from"),
        effective_to=f.values.get("effective_to"),
        published_at=f.values.get("published"),
        series_hint=f.values.get("series"),
        topic=f.values.get("topic"),
        subjects=list(subjects) if isinstance(subjects, list) else [],
        supersedes_hint=[str(s) for s in supersedes]
        if isinstance(supersedes, list)
        else ([str(supersedes)] if supersedes else []),
        field_provenance=f.meta,
    )
