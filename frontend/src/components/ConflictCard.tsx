"use client";

import clsx from "clsx";
import { AlertTriangle, ChevronDown, Gavel, ScanSearch } from "lucide-react";
import { useState } from "react";
import { fmtDate, humanize, RULE_NAMES, SEVERITY_TONE, tierLabel } from "@/lib/format";
import type { ConflictView, Position, RuleId } from "@/lib/types";
import { Badge, Card } from "./ui";

export const OUTCOME_LABEL: Record<RuleId, string> = {
  Q: "Quarantined",
  R0: "Not in force",
  R1: "Superseded",
  R2: "Outranked",
  R3: "General rule",
  R4: "Older version",
  R5: "Undecided",
};

export function outcomeBadge(p: Pick<Position, "outcome" | "eliminated_by">) {
  if (p.outcome === "winner") return <Badge tone="emerald">Applies</Badge>;
  if (p.outcome === "scope_winner") return <Badge tone="emerald">Applies (scope)</Badge>;
  if (p.outcome === "agrees") return <Badge tone="sky">Agrees</Badge>;
  if (p.outcome === "unresolved") return <Badge tone="rose">Disputed</Badge>;
  const rule = p.eliminated_by ?? "R5";
  return (
    <Badge tone="slate" title={RULE_NAMES[rule]}>
      {OUTCOME_LABEL[rule]} · {rule}
    </Badge>
  );
}

export function XRayLink({ onClick, label = "View source" }: { onClick: () => void; label?: string }) {
  return (
    <button
      onClick={onClick}
      className="inline-flex items-center gap-1 text-xs font-medium text-blue-700 hover:underline dark:text-blue-300"
    >
      <ScanSearch className="h-3.5 w-3.5" aria-hidden /> {label}
    </button>
  );
}

export function ConflictCard({
  conflict,
  onXRay,
  defaultOpen = true,
}: {
  conflict: ConflictView;
  onXRay: (p: Position) => void;
  defaultOpen?: boolean;
}) {
  const [showChain, setShowChain] = useState(defaultOpen);
  const r = conflict.resolution;
  const unresolved = r.status === "unresolved";
  return (
    <Card className={clsx("pop-in overflow-hidden", unresolved && "ring-1 ring-rose-300 dark:ring-rose-900")} data-testid="conflict-card">
      <div className="flex flex-wrap items-center gap-2 border-b border-slate-100 px-4 py-2.5 dark:border-slate-800">
        <Gavel className="h-4 w-4 text-slate-500" aria-hidden />
        <h3 className="text-sm font-semibold">{conflict.label}</h3>
        <Badge tone={SEVERITY_TONE[conflict.severity]}>{conflict.severity}</Badge>
        <Badge tone="slate">{humanize(conflict.conflict_type.toLowerCase())}</Badge>
        <span className="ml-auto text-xs text-slate-500">
          {unresolved ? (
            <span className="font-semibold text-rose-600 dark:text-rose-400">Cannot determine</span>
          ) : (
            <>
              Decided by{" "}
              <span className="font-semibold text-slate-800 dark:text-slate-200" data-testid="deciding-rule">
                {r.deciding_rule} {r.deciding_rule ? RULE_NAMES[r.deciding_rule] : ""}
              </span>
            </>
          )}
        </span>
      </div>

      {r.flags.includes("possible_unofficial_update") && (
        <div className="flex items-start gap-2 bg-rose-50 px-4 py-2 text-xs text-rose-800 dark:bg-rose-950/40 dark:text-rose-200">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
          <span>
            <b>Possible unofficial update.</b> A lower-authority source is newer than the official one it contradicts.
            {conflict.who_should_decide && <> Escalate to the <b>{conflict.who_should_decide}</b>.</>}
          </span>
        </div>
      )}
      {unresolved && !r.flags.includes("possible_unofficial_update") && conflict.who_should_decide && (
        <div className="bg-rose-50 px-4 py-2 text-xs text-rose-800 dark:bg-rose-950/40 dark:text-rose-200">
          No precedence rule decides. This should be settled by the <b>{conflict.who_should_decide}</b>.
        </div>
      )}

      <ul className="divide-y divide-slate-100 dark:divide-slate-800">
        {conflict.positions.map((p) => (
          <li
            key={p.assertion_id}
            className={clsx(
              "grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 px-4 py-2.5 sm:grid-cols-[6.5rem_1fr_auto]",
              p.outcome === "eliminated" && "opacity-70",
            )}
          >
            <div
              className={clsx(
                "text-lg font-semibold tabular-nums",
                p.outcome === "eliminated" && "text-slate-400 line-through decoration-2 dark:text-slate-500",
                (p.outcome === "winner" || p.outcome === "scope_winner") && "text-emerald-700 dark:text-emerald-300",
                p.outcome === "unresolved" && "text-rose-700 dark:text-rose-300",
              )}
            >
              {p.value_display}
              {p.scope !== "all" && <div className="text-[10px] font-normal text-slate-500">{humanize(p.scope)}</div>}
            </div>
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-1.5 text-sm">
                <span className="font-medium">{p.document_title}</span>
                {p.version_label && !p.document_title.includes(p.version_label) && (
                  <span className="text-slate-500">{p.version_label}</span>
                )}
                <span className="text-xs text-slate-500">· {fmtDate(p.effective_from ?? p.published_at)}</span>
                {p.metadata_inferred && (
                  <Badge tone="amber" title="Dates inferred from the text, not explicit metadata">
                    inferred date
                  </Badge>
                )}
              </div>
              <div className="text-xs text-slate-500">
                Tier {p.authority_tier} · {tierLabel(p.authority_tier)} · {humanize(p.doc_type)}
              </div>
              <blockquote className="mt-1 border-l-2 border-slate-200 pl-2 text-xs italic text-slate-600 dark:border-slate-700 dark:text-slate-400">
                “{p.quote}”
              </blockquote>
              <XRayLink onClick={() => onXRay(p)} />
            </div>
            <div className="col-start-2 sm:col-start-3 sm:row-start-1">{outcomeBadge(p)}</div>
          </li>
        ))}
      </ul>

      <button
        onClick={() => setShowChain((s) => !s)}
        aria-expanded={showChain}
        className="flex w-full items-center gap-1 border-t border-slate-100 px-4 py-2 text-left text-xs font-medium text-slate-600 hover:bg-slate-50 dark:border-slate-800 dark:text-slate-300 dark:hover:bg-slate-800/50"
      >
        <ChevronDown className={clsx("h-3.5 w-3.5 transition", showChain && "rotate-180")} aria-hidden />
        Rule chain ({r.rule_chain.length} rules evaluated) · strength {Math.round(r.strength * 100)}%
      </button>
      {showChain && (
        <ol className="space-y-1 px-4 pb-3" data-testid="rule-chain">
          {r.rule_chain.map((e, i) => (
            <li key={`${e.rule}-${i}`} className="flex gap-2 text-xs">
              <span
                className={clsx(
                  "mt-0.5 inline-flex h-5 w-8 shrink-0 items-center justify-center rounded font-mono text-[10px] font-bold",
                  e.applied
                    ? "bg-slate-900 text-white dark:bg-slate-100 dark:text-slate-900"
                    : "bg-slate-100 text-slate-400 dark:bg-slate-800",
                )}
              >
                {e.rule}
              </span>
              <span className={clsx(!e.applied && "text-slate-400")}>
                <b>{RULE_NAMES[e.rule]}.</b> {e.note}
              </span>
            </li>
          ))}
          <li className="pt-1 text-xs text-slate-600 dark:text-slate-300">{r.rationale}</li>
        </ol>
      )}
    </Card>
  );
}
