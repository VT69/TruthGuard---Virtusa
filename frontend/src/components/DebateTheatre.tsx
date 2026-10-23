"use client";

import clsx from "clsx";
import { ArrowRight, Check, RefreshCcw, ShieldAlert, X } from "lucide-react";
import { SEVERITY_TONE } from "@/lib/format";
import type { AnswerClaim, Objection, TraceEvent } from "@/lib/types";
import { Badge, Card, SectionTitle } from "./ui";

const NODES = [
  { id: "planner", label: "Planner", event: "plan.created" },
  { id: "retriever", label: "Retriever", event: "retrieval.completed" },
  { id: "analyst", label: "Analyst", event: "evidence.analyzed" },
  { id: "resolver", label: "Resolver", event: "resolution.applied" },
  { id: "synthesizer", label: "Synthesizer", event: "draft.created" },
  { id: "critic", label: "Critic", event: "critic.completed" },
  { id: "finalizer", label: "Finalizer", event: "answer.final" },
];

interface Check_ {
  name: string;
  passed: boolean;
  detail: string;
}

export function DebateTheatre({ events, running }: { events: TraceEvent[]; running: boolean }) {
  const seen = new Set(events.map((e) => e.event));
  const lastIdx = NODES.reduce((acc, n, i) => (seen.has(n.event) ? i : acc), -1);
  const revisions = events.filter((e) => e.event === "revision.started");
  const objections = events.filter((e) => e.event === "critic.objection").map((e) => e.payload as unknown as Objection);
  const criticRuns = events.filter((e) => e.event === "critic.completed");
  const lastCritic = criticRuns.at(-1)?.payload as { checks?: Check_[]; counter_queries?: string[]; new_sources?: { title: string }[] } | undefined;
  const drafts = events.filter((e) => e.event === "draft.created").map((e) => e.payload as { claims: AnswerClaim[]; revision: number });
  const latency = (node: string) =>
    events.filter((e) => e.node === node).reduce((m, e) => Math.max(m, e.latency_ms), 0);
  const tokens = (node: string) => events.filter((e) => e.node === node).reduce((m, e) => Math.max(m, e.tokens), 0);

  return (
    <Card className="p-4" data-testid="debate-theatre">
      <SectionTitle right={revisions.length > 0 && <Badge tone="amber">{revisions.length} revision round(s)</Badge>}>
        Debate theatre
      </SectionTitle>
      <ol className="flex flex-wrap items-center gap-1.5" aria-label="Agent graph progress">
        {NODES.map((n, i) => {
          const done = seen.has(n.event);
          const active = running && !done && i === lastIdx + 1;
          return (
            <li key={n.id} className="flex items-center gap-1.5">
              <div
                className={clsx(
                  "rounded-lg border px-2.5 py-1.5 text-xs transition",
                  done && "border-blue-300 bg-blue-50 text-blue-900 dark:border-blue-800 dark:bg-blue-950/50 dark:text-blue-100",
                  active && "node-active border-blue-500 bg-white dark:bg-slate-900",
                  !done && !active && "border-slate-200 text-slate-400 dark:border-slate-800",
                )}
                aria-current={active ? "step" : undefined}
              >
                <div className="font-semibold">{n.label}</div>
                <div className="tabular-nums text-[10px] opacity-80">
                  {done ? `${Math.round(latency(n.id))} ms${tokens(n.id) ? ` · ${tokens(n.id)} tok` : ""}` : active ? "working…" : "—"}
                </div>
              </div>
              {i < NODES.length - 1 && <ArrowRight className="h-3.5 w-3.5 text-slate-300" aria-hidden />}
            </li>
          );
        })}
        {revisions.length > 0 && (
          <li className="flex items-center gap-1 text-xs text-amber-700 dark:text-amber-300">
            <RefreshCcw className="h-3.5 w-3.5" aria-hidden /> critic sent it back {revisions.length}×
          </li>
        )}
      </ol>

      {lastCritic?.checks && (
        <div className="mt-3 grid gap-1 sm:grid-cols-2 lg:grid-cols-4" aria-label="Critic checks">
          {lastCritic.checks.map((c) => (
            <div key={c.name} className="flex items-start gap-1.5 rounded-md bg-slate-50 px-2 py-1.5 text-xs dark:bg-slate-800/60">
              {c.passed ? (
                <Check className="mt-0.5 h-3.5 w-3.5 shrink-0 text-emerald-600" aria-label="passed" />
              ) : (
                <X className="mt-0.5 h-3.5 w-3.5 shrink-0 text-rose-600" aria-label="failed" />
              )}
              <span>
                <b className="font-medium">{c.name.replace(/_/g, " ")}</b>
                <span className="block text-slate-500">{c.detail}</span>
              </span>
            </div>
          ))}
        </div>
      )}
      {lastCritic?.counter_queries && lastCritic.counter_queries.length > 0 && (
        <details className="mt-2 text-xs text-slate-500">
          <summary className="cursor-pointer">Critic counter-searches ({lastCritic.counter_queries.length})</summary>
          <ul className="ml-4 mt-1 list-disc">
            {lastCritic.counter_queries.map((q) => (
              <li key={q}>{q}</li>
            ))}
          </ul>
        </details>
      )}

      {objections.length > 0 && (
        <div className="mt-3 space-y-1.5" aria-label="Critic objections">
          {objections.map((o) => (
            <div key={o.id} className="pop-in flex items-start gap-2 rounded-lg border border-rose-200 bg-rose-50/60 px-3 py-2 text-xs dark:border-rose-900 dark:bg-rose-950/30">
              <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0 text-rose-600" aria-hidden />
              <div>
                <div className="flex flex-wrap items-center gap-1.5">
                  <b>{o.type.replace(/_/g, " ").toLowerCase()}</b>
                  <Badge tone={SEVERITY_TONE[o.severity]}>{o.severity}</Badge>
                </div>
                <div className="text-slate-700 dark:text-slate-300">{o.evidence}</div>
                <div className="text-slate-500">Fix: {o.suggested_fix}</div>
              </div>
            </div>
          ))}
        </div>
      )}

      {drafts.length > 1 && (
        <div className="mt-3" aria-label="Revision diff">
          <SectionTitle>Revision diff</SectionTitle>
          {drafts.slice(1).map((d, i) => {
            const prev = drafts[i];
            const before = new Set(prev.claims.map((c) => c.text));
            const after = new Set(d.claims.map((c) => c.text));
            return (
              <div key={d.revision} className="space-y-1 text-sm">
                {prev.claims
                  .filter((c) => !after.has(c.text))
                  .map((c) => (
                    <div key={`o-${c.id}`} className="text-rose-700 line-through decoration-2 dark:text-rose-300">
                      − {c.text}
                    </div>
                  ))}
                {d.claims
                  .filter((c) => !before.has(c.text))
                  .map((c) => (
                    <div key={`n-${c.id}`} className="text-emerald-700 dark:text-emerald-300">
                      + {c.text}
                    </div>
                  ))}
              </div>
            );
          })}
        </div>
      )}
      {events.length === 0 && <p className="mt-2 text-sm text-slate-500">Ask a question to watch the agents work.</p>}
    </Card>
  );
}
