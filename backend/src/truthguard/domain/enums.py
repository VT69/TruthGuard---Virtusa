from __future__ import annotations

from enum import StrEnum


class AnswerStatus(StrEnum):
    VERIFIED = "VERIFIED"
    RESOLVED_CONFLICT = "RESOLVED_CONFLICT"
    UNRESOLVED_CONFLICT = "UNRESOLVED_CONFLICT"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


class ConflictType(StrEnum):
    VALUE_MISMATCH = "VALUE_MISMATCH"
    TEMPORAL_SUPERSESSION = "TEMPORAL_SUPERSESSION"
    AUTHORITY_CLASH = "AUTHORITY_CLASH"
    SCOPE_OVERLAP = "SCOPE_OVERLAP"
    MODALITY_CONFLICT = "MODALITY_CONFLICT"
    NUMERIC_DRIFT = "NUMERIC_DRIFT"


class Severity(StrEnum):
    CRITICAL = "critical"
    MAJOR = "major"
    MINOR = "minor"


class Relation(StrEnum):
    SUPERSEDES = "supersedes"
    AMENDS = "amends"
    CLARIFIES = "clarifies"
    REFERENCES = "references"

    @property
    def is_superseding(self) -> bool:
        return self in (Relation.SUPERSEDES, Relation.AMENDS)


class Provenance(StrEnum):
    EXPLICIT = "explicit"
    INFERRED = "inferred"
    LLM = "llm"


class Modality(StrEnum):
    MUST = "must"
    MAY = "may"
    MUST_NOT = "must_not"
    STATES = "states"


class ValueKind(StrEnum):
    MONEY = "money"
    PERCENT = "percent"
    DAYS = "days"
    MONTHS = "months"
    YEARS = "years"
    COUNT = "count"
    MODALITY = "modality"
    TEXT = "text"


class RuleId(StrEnum):
    QUARANTINE = "Q"
    R0 = "R0"
    R1 = "R1"
    R2 = "R2"
    R3 = "R3"
    R4 = "R4"
    R5 = "R5"


class ResolutionStatus(StrEnum):
    NO_CONFLICT = "no_conflict"
    RESOLVED = "resolved"
    SCOPE_SPLIT = "scope_split"
    UNRESOLVED = "unresolved"


class ObjectionType(StrEnum):
    FABRICATED_QUOTE = "FABRICATED_QUOTE"
    NOT_ENTAILED = "NOT_ENTAILED"
    STALE_CITATION = "STALE_CITATION"
    BLENDED_VALUE = "BLENDED_VALUE"
    NEW_CONFLICT = "NEW_CONFLICT"
    UNDISCLOSED_CONFLICT = "UNDISCLOSED_CONFLICT"
    UNSUPPORTED_CLAIM = "UNSUPPORTED_CLAIM"
    IGNORED_RESOLUTION = "IGNORED_RESOLUTION"


class DocStatus(StrEnum):
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    NOT_YET_EFFECTIVE = "not_yet_effective"
    EXPIRED = "expired"
    QUARANTINED = "quarantined"


class QueryMode(StrEnum):
    TRUTHGUARD = "truthguard"
    BASELINE = "baseline"


class JobStage(StrEnum):
    QUEUED = "queued"
    PARSE = "parse"
    METADATA = "metadata"
    LINEAGE = "lineage"
    CHUNK = "chunk"
    INDEX = "index"
    ASSERTIONS = "assertions"
    AUDIT = "audit"
    DONE = "done"
    FAILED = "failed"
