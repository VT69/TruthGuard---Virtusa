"""Domain models shared by every layer. Pydantic v2, no I/O."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from truthguard.domain.enums import (
    AnswerStatus,
    ConflictType,
    DocStatus,
    Modality,
    ObjectionType,
    Provenance,
    Relation,
    ResolutionStatus,
    RuleId,
    Severity,
    ValueKind,
)


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=False)


# --------------------------------------------------------------------------- documents


class FieldMeta(_Model):
    provenance: Provenance
    confidence: float = Field(ge=0.0, le=1.0)


class Section(_Model):
    heading_path: list[str]
    text: str
    char_start: int
    char_end: int
    page: int | None = None


class ParsedDocument(_Model):
    raw_text: str
    sections: list[Section]
    frontmatter: dict[str, Any] = Field(default_factory=dict)
    properties: dict[str, Any] = Field(default_factory=dict)
    fmt: str


class DocumentMetadata(_Model):
    doc_key: str | None = None
    title: str
    doc_type: str
    authority_tier: int
    version_label: str | None = None
    effective_from: date | None = None
    effective_to: date | None = None
    published_at: date | None = None
    series_hint: str | None = None
    topic: str | None = None
    subjects: list[str] = Field(default_factory=list)
    supersedes_hint: list[str] = Field(default_factory=list)
    field_provenance: dict[str, FieldMeta] = Field(default_factory=dict)

    @property
    def is_inferred(self) -> bool:
        return any(m.provenance != Provenance.EXPLICIT for m in self.field_provenance.values())


class Document(_Model):
    id: str
    series_id: str
    title: str
    doc_type: str
    authority_tier: int
    version_label: str | None = None
    effective_from: date | None = None
    effective_to: date | None = None
    published_at: date | None = None
    topic: str | None = None
    status: DocStatus = DocStatus.ACTIVE
    source_uri: str
    sha256: str
    fmt: str
    origin: Literal["seed", "user"] = "seed"
    injection_flag: bool = False
    field_provenance: dict[str, FieldMeta] = Field(default_factory=dict)

    @property
    def sort_date(self) -> date | None:
        return self.effective_from or self.published_at

    def label(self) -> str:
        parts = [self.title]
        if self.version_label and self.version_label not in self.title:
            parts.append(self.version_label)
        if self.sort_date:
            parts.append(self.sort_date.isoformat())
        return " · ".join(parts)


class Chunk(_Model):
    id: str
    document_id: str
    ord: int
    section_path: list[str]
    text: str
    char_start: int
    char_end: int
    injection_flag: bool = False


class LineageEdge(_Model):
    id: str
    from_doc: str
    to_doc: str
    relation: Relation
    scope_section: str | None = None
    evidence_span: str
    provenance: Provenance
    confidence: float = Field(ge=0.0, le=1.0)


class Assertion(_Model):
    id: str
    chunk_id: str
    document_id: str
    subject_id: str
    attribute: str
    kind: ValueKind
    value_norm: str
    value_display: str
    value_num: float | None = None
    unit: str | None = None
    scope: str = "all"
    modality: Modality = Modality.STATES
    effective_from: date | None = None
    effective_to: date | None = None
    quote: str
    quote_start: int
    quote_end: int
    section_path: list[str] = Field(default_factory=list)
    provenance: Provenance = Provenance.INFERRED
    confidence: float = 1.0
    quarantined: bool = False
    inherited_slot: bool = False

    @property
    def slot_key(self) -> str:
        return f"{self.subject_id}|{self.attribute}"


# --------------------------------------------------------------------------- analysis


class ConflictSet(_Model):
    id: str
    slot_key: str
    subject_id: str
    attribute: str
    assertion_ids: list[str]
    distinct_values: list[str]
    conflict_type: ConflictType
    secondary_types: list[ConflictType] = Field(default_factory=list)
    severity: Severity
    topic: str | None = None


class RuleEvaluation(_Model):
    rule: RuleId
    applied: bool
    eliminated: list[str] = Field(default_factory=list)
    note: str


class Resolution(_Model):
    conflict_id: str
    slot_key: str
    status: ResolutionStatus
    winner_assertion_ids: list[str] = Field(default_factory=list)
    winners_by_scope: dict[str, str] = Field(default_factory=dict)
    deciding_rule: RuleId | None = None
    rule_chain: list[RuleEvaluation] = Field(default_factory=list)
    eliminated_by: dict[str, RuleId] = Field(default_factory=dict)
    rationale: str
    as_of: date
    strength: float = 0.0
    flags: list[str] = Field(default_factory=list)


class SlotRef(_Model):
    subject_id: str
    attribute: str
    scope: str | None = None

    @property
    def key(self) -> str:
        return f"{self.subject_id}|{self.attribute}"


class QueryPlan(_Model):
    question: str
    intent: Literal["lookup", "compare", "overview", "unknown"] = "lookup"
    as_of: date
    slots: list[SlotRef] = Field(default_factory=list)
    entities: list[str] = Field(default_factory=list)
    unknown_entities: list[str] = Field(default_factory=list)
    sub_queries: list[str] = Field(default_factory=list)
    claimed_values: list[str] = Field(default_factory=list)


class EvidenceItem(_Model):
    chunk: Chunk
    document: Document
    score: float
    sources: list[str] = Field(default_factory=list)
    temporal_status: DocStatus = DocStatus.ACTIVE
    assertions: list[Assertion] = Field(default_factory=list)


class EvidenceSet(_Model):
    items: list[EvidenceItem] = Field(default_factory=list)
    edges: list[LineageEdge] = Field(default_factory=list)
    diagnostics: dict[str, Any] = Field(default_factory=dict)

    def assertion_by_id(self) -> dict[str, Assertion]:
        return {a.id: a for it in self.items for a in it.assertions}

    def document_by_id(self) -> dict[str, Document]:
        return {it.document.id: it.document for it in self.items}

    def chunk_by_id(self) -> dict[str, Chunk]:
        return {it.chunk.id: it.chunk for it in self.items}


class SlotRow(_Model):
    slot_key: str
    subject_id: str
    attribute: str
    requested_scope: str | None = None
    assertion_ids: list[str]


class SlotTable(_Model):
    rows: list[SlotRow] = Field(default_factory=list)
    conflicts: list[ConflictSet] = Field(default_factory=list)


class SlotAnalysis(_Model):
    """One requested slot after analysis and resolution."""

    key: str
    row: SlotRow
    conflict: ConflictSet | None = None
    resolution: Resolution | None = None
    quarantined_ids: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------- answers


class Citation(_Model):
    chunk_id: str
    document_id: str
    quote: str
    char_start: int | None = None
    char_end: int | None = None
    verified: bool | None = None


class AnswerClaim(_Model):
    id: str
    text: str
    slot_key: str | None = None
    scope: str | None = None
    value_norm: str | None = None
    value_display: str | None = None
    verdict: Literal["winner", "position", "superseded", "info", "corroborated"] = "winner"
    citations: list[Citation] = Field(default_factory=list)
    entailment: float | None = None
    contradiction: float | None = None


class DraftAnswer(_Model):
    summary: str
    claims: list[AnswerClaim] = Field(default_factory=list)
    disclosed_conflict_ids: list[str] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)
    revision: int = 0


class Objection(_Model):
    id: str
    type: ObjectionType
    severity: Severity
    claim_id: str | None = None
    slot_key: str | None = None
    evidence: str
    suggested_fix: str

    @property
    def blocking(self) -> bool:
        return self.severity in (Severity.CRITICAL, Severity.MAJOR)


class CriticCheck(_Model):
    name: str
    passed: bool
    detail: str


class CriticReport(_Model):
    objections: list[Objection] = Field(default_factory=list)
    checks: list[CriticCheck] = Field(default_factory=list)
    counter_queries: list[str] = Field(default_factory=list)
    new_assertion_ids: list[str] = Field(default_factory=list)


class Position(_Model):
    assertion_id: str
    document_id: str
    document_title: str
    version_label: str | None = None
    effective_from: date | None = None
    published_at: date | None = None
    authority_tier: int
    doc_type: str
    quote: str
    chunk_id: str
    value_display: str
    value_norm: str
    scope: str
    outcome: Literal["winner", "eliminated", "unresolved", "scope_winner", "agrees"]
    eliminated_by: RuleId | None = None
    temporal_status: DocStatus = DocStatus.ACTIVE
    metadata_inferred: bool = False


class ConflictView(_Model):
    conflict_id: str
    slot_key: str
    label: str
    conflict_type: ConflictType
    severity: Severity
    positions: list[Position]
    resolution: Resolution
    who_should_decide: str | None = None


class ConfidenceBreakdown(_Model):
    retrieval_coverage: float
    weakest_entailment: float
    resolution_strength: float
    metadata_penalty: float
    objection_penalty: float
    score: float


class Usage(_Model):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0
    llm_calls: int = 0
    cache_hits: int = 0
    latency_ms: float = 0.0


class FinalAnswer(_Model):
    trace_id: str
    question: str
    as_of: date
    mode: str = "truthguard"
    status: AnswerStatus
    answer_text: str
    claims: list[AnswerClaim] = Field(default_factory=list)
    conflicts: list[ConflictView] = Field(default_factory=list)
    confidence: ConfidenceBreakdown | None = None
    residual_objections: list[Objection] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)
    injection_flags: list[str] = Field(default_factory=list)
    stale_citations: list[str] = Field(default_factory=list)
    blend_flags: list[str] = Field(default_factory=list)
    revisions: int = 0
    usage: Usage = Field(default_factory=Usage)


class TraceEvent(_Model):
    trace_id: str
    seq: int
    event: str
    node: str
    ts: datetime
    payload: dict[str, Any] = Field(default_factory=dict)
    tokens: int = 0
    cost_usd: float = 0.0
    latency_ms: float = 0.0


class ValueFlag(_Model):
    """How one value stated by a baseline answer relates to the corpus (split-screen overlay)."""

    value_norm: str
    value_display: str
    verdict: Literal["winner", "position", "stale", "quarantined", "blended"]
    rule: RuleId | None = None
    documents: list[str] = Field(default_factory=list)
    explanation: str


class BaselineAnswer(_Model):
    trace_id: str
    question: str
    as_of: date
    mode: str
    status: AnswerStatus
    answer_text: str
    stated_values: list[str] = Field(default_factory=list)
    citations: list[Citation] = Field(default_factory=list)
    mentions_conflict: bool = False
    value_flags: list[ValueFlag] = Field(default_factory=list)
    usage: Usage = Field(default_factory=Usage)
