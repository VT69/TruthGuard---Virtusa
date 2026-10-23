import type { AnswerStatus, RuleId, Severity } from "./types";

export const RULE_NAMES: Record<RuleId, string> = {
  Q: "Quarantine",
  R0: "Temporal validity",
  R1: "Explicit supersession",
  R2: "Authority tier",
  R3: "Specificity",
  R4: "Recency in series",
  R5: "No decision",
};

export const STATUS_META: Record<AnswerStatus, { label: string; tone: string; blurb: string }> = {
  VERIFIED: {
    label: "Verified",
    tone: "emerald",
    blurb: "All claims are supported and the sources agree.",
  },
  RESOLVED_CONFLICT: {
    label: "Conflict resolved",
    tone: "amber",
    blurb: "Sources disagree; a precedence rule decides which one applies.",
  },
  UNRESOLVED_CONFLICT: {
    label: "Unresolved conflict",
    tone: "rose",
    blurb: "Sources disagree and no rule decides. TruthGuard will not pick a side.",
  },
  INSUFFICIENT_EVIDENCE: {
    label: "Insufficient evidence",
    tone: "slate",
    blurb: "The corpus does not answer this question.",
  },
};

export const SEVERITY_TONE: Record<Severity, string> = {
  critical: "rose",
  major: "amber",
  minor: "sky",
};

export const USD_TO_INR = 84;

export function fmtDate(d: string | null | undefined): string {
  if (!d) return "undated";
  const dt = new Date(`${d}T00:00:00`);
  return dt.toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });
}

const ACRONYMS: Record<string, string> = { Faq: "FAQ", Kyc: "KYC", Fd: "FD" };

export function humanize(s: string): string {
  return s
    .replace(/_/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase())
    .replace(/\b(Faq|Kyc|Fd)\b/g, (w) => ACRONYMS[w] ?? w);
}

export function pct(x: number | null | undefined): string {
  return x === null || x === undefined ? "n/a" : `${Math.round(x * 100)}%`;
}

export function tierLabel(t: number): string {
  return ["", "Regulatory / board", "Official policy", "Product terms", "FAQ / help", "Memo / email"][t] ?? `Tier ${t}`;
}

/** Tailwind classes per tone, spelled out so the compiler keeps them. */
export const TONE: Record<string, { bg: string; text: string; border: string; dot: string; soft: string }> = {
  emerald: {
    bg: "bg-emerald-600",
    text: "text-emerald-700 dark:text-emerald-300",
    border: "border-emerald-300 dark:border-emerald-800",
    dot: "bg-emerald-500",
    soft: "bg-emerald-50 dark:bg-emerald-950/40",
  },
  amber: {
    bg: "bg-amber-500",
    text: "text-amber-700 dark:text-amber-300",
    border: "border-amber-300 dark:border-amber-800",
    dot: "bg-amber-500",
    soft: "bg-amber-50 dark:bg-amber-950/40",
  },
  rose: {
    bg: "bg-rose-600",
    text: "text-rose-700 dark:text-rose-300",
    border: "border-rose-300 dark:border-rose-800",
    dot: "bg-rose-500",
    soft: "bg-rose-50 dark:bg-rose-950/40",
  },
  sky: {
    bg: "bg-sky-600",
    text: "text-sky-700 dark:text-sky-300",
    border: "border-sky-300 dark:border-sky-800",
    dot: "bg-sky-500",
    soft: "bg-sky-50 dark:bg-sky-950/40",
  },
  slate: {
    bg: "bg-slate-500",
    text: "text-slate-700 dark:text-slate-300",
    border: "border-slate-300 dark:border-slate-700",
    dot: "bg-slate-400",
    soft: "bg-slate-50 dark:bg-slate-900/60",
  },
};
