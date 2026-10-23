// Mirrors backend/src/truthguard/domain/models.py (JSON mode).

export type AnswerStatus = "VERIFIED" | "RESOLVED_CONFLICT" | "UNRESOLVED_CONFLICT" | "INSUFFICIENT_EVIDENCE";
export type RuleId = "Q" | "R0" | "R1" | "R2" | "R3" | "R4" | "R5";
export type Severity = "critical" | "major" | "minor";

export interface Citation {
  chunk_id: string;
  document_id: string;
  quote: string;
  char_start: number | null;
  char_end: number | null;
  verified: boolean | null;
}

export interface AnswerClaim {
  id: string;
  text: string;
  slot_key: string | null;
  scope: string | null;
  value_norm: string | null;
  value_display: string | null;
  verdict: "winner" | "position" | "superseded" | "info" | "corroborated";
  citations: Citation[];
  entailment: number | null;
  contradiction: number | null;
}

export interface RuleEvaluation {
  rule: RuleId;
  applied: boolean;
  eliminated: string[];
  note: string;
}

export interface Resolution {
  conflict_id: string;
  slot_key: string;
  status: "no_conflict" | "resolved" | "scope_split" | "unresolved";
  winner_assertion_ids: string[];
  winners_by_scope: Record<string, string>;
  deciding_rule: RuleId | null;
  rule_chain: RuleEvaluation[];
  eliminated_by: Record<string, RuleId>;
  rationale: string;
  as_of: string;
  strength: number;
  flags: string[];
}

export interface Position {
  assertion_id: string;
  document_id: string;
  document_title: string;
  version_label: string | null;
  effective_from: string | null;
  published_at: string | null;
  authority_tier: number;
  doc_type: string;
  quote: string;
  chunk_id: string;
  value_display: string;
  value_norm: string;
  scope: string;
  outcome: "winner" | "eliminated" | "unresolved" | "scope_winner" | "agrees";
  eliminated_by: RuleId | null;
  temporal_status: string;
  metadata_inferred: boolean;
}

export interface ConflictView {
  conflict_id: string;
  slot_key: string;
  label: string;
  conflict_type: string;
  severity: Severity;
  positions: Position[];
  resolution: Resolution;
  who_should_decide: string | null;
}

export interface ConfidenceBreakdown {
  retrieval_coverage: number;
  weakest_entailment: number;
  resolution_strength: number;
  metadata_penalty: number;
  objection_penalty: number;
  score: number;
}

export interface Usage {
  prompt_tokens: number;
  completion_tokens: number;
  cost_usd: number;
  llm_calls: number;
  cache_hits: number;
  latency_ms: number;
}

export interface Objection {
  id: string;
  type: string;
  severity: Severity;
  claim_id: string | null;
  slot_key: string | null;
  evidence: string;
  suggested_fix: string;
}

export interface FinalAnswer {
  trace_id: string;
  question: string;
  as_of: string;
  mode: string;
  status: AnswerStatus;
  answer_text: string;
  claims: AnswerClaim[];
  conflicts: ConflictView[];
  confidence: ConfidenceBreakdown | null;
  residual_objections: Objection[];
  missing_evidence: string[];
  injection_flags: string[];
  stale_citations: string[];
  blend_flags: string[];
  revisions: number;
  usage: Usage;
}

export interface ValueFlag {
  value_norm: string;
  value_display: string;
  verdict: "winner" | "position" | "stale" | "quarantined" | "blended";
  rule: RuleId | null;
  documents: string[];
  explanation: string;
}

export interface BaselineAnswer {
  trace_id: string;
  question: string;
  as_of: string;
  mode: string;
  status: AnswerStatus;
  answer_text: string;
  stated_values: string[];
  citations: Citation[];
  mentions_conflict: boolean;
  value_flags: ValueFlag[];
  usage: Usage;
}

export interface TraceEvent {
  trace_id: string;
  seq: number;
  event: string;
  node: string;
  ts: string;
  payload: Record<string, unknown>;
  tokens: number;
  cost_usd: number;
  latency_ms: number;
}

export interface Meta {
  version: string;
  today: string;
  llm_mode: string;
  demo_mode: boolean;
  models: Record<string, string>;
  backends: Record<string, string>;
  corpus: Record<string, number>;
}

export interface HealthPosition {
  assertion_id: string;
  document_id: string;
  title: string;
  version: string | null;
  doc_type: string;
  tier: number;
  date: string | null;
  value: string;
  scope: string;
  quote: string;
  chunk_id: string;
  outcome: string;
  eliminated_by: string | null;
}

export interface HealthConflict {
  id: string;
  slot_key: string;
  label: string;
  topic: string;
  type: string;
  secondary_types: string[];
  severity: Severity;
  values: string[];
  status: string;
  deciding_rule: string | null;
  flags: string[];
  rationale: string;
  rule_chain: RuleEvaluation[];
  positions: HealthPosition[];
  owner: string | null;
}

export interface Health {
  as_of: string;
  totals: {
    conflicts: number;
    unresolved: number;
    by_severity: Record<string, number>;
    by_type: Record<string, number>;
    by_topic: Record<string, number>;
    documents: number;
    stale_documents: number;
    quarantined_documents: number;
  };
  conflicts: HealthConflict[];
  heatmap: {
    topics: string[];
    documents: Record<string, { id: string; title: string; version: string | null; doc_type: string }[]>;
    cells: { topic: string; document_id: string; severity: Severity }[];
  };
  stale_documents: {
    document_id: string;
    title: string;
    version: string | null;
    doc_type: string;
    status: string;
    losing_slots: string[];
    rules: string[];
  }[];
  suggestions: { document_id: string | null; action: string; text: string }[];
  quarantined: { document_id: string; title: string }[];
}

export interface LineageEdge {
  id: string;
  from_doc: string;
  to_doc: string;
  relation: string;
  scope_section: string | null;
  evidence_span: string;
  provenance: string;
  confidence: number;
}

export interface LineageSeries {
  series_id: string;
  title: string;
  topic: string | null;
  documents: {
    id: string;
    title: string;
    version: string | null;
    doc_type: string;
    tier: number;
    effective_from: string | null;
    published_at: string | null;
    status: string;
    in_series: boolean;
    inferred_dates: boolean;
  }[];
  edges: LineageEdge[];
}

export interface Policy {
  version: number;
  order: RuleId[];
  tiers: Record<string, number>;
  default_tier: number;
  strength: Record<string, number>;
  rules: { R2: { escalate_if_lower_tier_newer: boolean }; R4: { cross_series: boolean } };
}

export interface DocumentDetail {
  document: {
    id: string;
    title: string;
    doc_type: string;
    authority_tier: number;
    version_label: string | null;
    effective_from: string | null;
    published_at: string | null;
    series_id: string;
    injection_flag: boolean;
    field_provenance: Record<string, { provenance: string; confidence: number }>;
  };
  raw_text: string;
  status: string;
  chunks: { id: string; section_path: string[]; char_start: number; char_end: number; injection_flag: boolean }[];
  edges: LineageEdge[];
}
