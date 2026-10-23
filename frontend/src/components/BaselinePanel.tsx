"use client";

import clsx from "clsx";
import { Bot } from "lucide-react";
import { RULE_NAMES } from "@/lib/format";
import type { BaselineAnswer, ValueFlag } from "@/lib/types";
import { Badge, Card, SectionTitle, Spinner } from "./ui";

const VERDICT_STYLE: Record<ValueFlag["verdict"], string> = {
  stale: "bg-rose-100 text-rose-900 decoration-rose-500 underline decoration-wavy decoration-2 dark:bg-rose-900/50 dark:text-rose-100",
  blended: "bg-rose-100 text-rose-900 decoration-rose-500 underline decoration-wavy decoration-2 dark:bg-rose-900/50 dark:text-rose-100",
  quarantined: "bg-rose-200 text-rose-900 dark:bg-rose-800 dark:text-rose-100",
  position: "bg-amber-100 text-amber-900 dark:bg-amber-900/40 dark:text-amber-100",
  winner: "bg-emerald-100 text-emerald-900 dark:bg-emerald-900/40 dark:text-emerald-100",
};

function highlight(text: string, flags: ValueFlag[]) {
  if (!flags.length) return text;
  const pattern = new RegExp(`(${flags.map((f) => f.value_display.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|")})`, "g");
  return text.split(pattern).map((part, i) => {
    const f = flags.find((x) => x.value_display === part);
    if (!f) return <span key={i}>{part}</span>;
    return (
      <mark key={i} className={clsx("rounded px-1 font-semibold", VERDICT_STYLE[f.verdict])} title={f.explanation}>
        {part}
      </mark>
    );
  });
}

export function BaselinePanel({
  answer,
  running,
  mode,
  onMode,
}: {
  answer: BaselineAnswer | null;
  running: boolean;
  mode: "B0" | "B1" | "B2";
  onMode: (m: "B0" | "B1" | "B2") => void;
}) {
  const bad = answer?.value_flags.filter((f) => ["stale", "blended", "quarantined"].includes(f.verdict)) ?? [];
  const silentPick =
    answer && !answer.mentions_conflict && answer.value_flags.some((f) => f.verdict === "position");
  return (
    <Card className={clsx("h-full p-4", (bad.length > 0 || silentPick) && "ring-2 ring-rose-400/70")} data-testid="baseline-panel">
      <SectionTitle
        right={
          <select
            aria-label="Baseline variant"
            value={mode}
            onChange={(e) => onMode(e.target.value as "B0" | "B1" | "B2")}
            className="rounded-md border border-slate-300 bg-white px-1.5 py-0.5 text-xs dark:border-slate-700 dark:bg-slate-900"
          >
            <option value="B0">B0 naive RAG</option>
            <option value="B1">B1 RAG + recency</option>
            <option value="B2">B2 self-critique</option>
          </select>
        }
      >
        <span className="inline-flex items-center gap-1.5">
          <Bot className="h-3.5 w-3.5" aria-hidden /> Naive RAG, side by side
        </span>
      </SectionTitle>
      {running && !answer && (
        <div className="flex items-center gap-2 text-sm text-slate-500">
          <Spinner /> answering…
        </div>
      )}
      {answer && (
        <div className="space-y-3">
          <p className="text-[15px] leading-relaxed" data-testid="baseline-answer">
            {highlight(answer.answer_text, answer.value_flags)}
          </p>
          <div className="flex flex-wrap gap-1.5">
            <Badge tone={answer.mentions_conflict ? "amber" : "slate"}>
              {answer.mentions_conflict ? "mentions a disagreement" : "answers confidently"}
            </Badge>
            <Badge tone="slate">{answer.status.replace(/_/g, " ").toLowerCase()}</Badge>
          </div>
          {answer.value_flags.length > 0 && (
            <ul className="space-y-1.5 text-xs">
              {answer.value_flags.map((f) => (
                <li key={f.value_norm} className="flex items-start gap-2">
                  <mark className={clsx("rounded px-1 font-semibold", VERDICT_STYLE[f.verdict])}>{f.value_display}</mark>
                  <span className="text-slate-600 dark:text-slate-300">
                    {f.verdict === "stale" && (
                      <b className="text-rose-700 dark:text-rose-300">
                        Stale{f.rule ? ` (${f.rule} ${RULE_NAMES[f.rule]})` : ""}:{" "}
                      </b>
                    )}
                    {f.verdict === "position" && !answer.mentions_conflict && (
                      <b className="text-rose-700 dark:text-rose-300">Silently picked one side: </b>
                    )}
                    {f.explanation}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
      {!answer && !running && <p className="text-sm text-slate-500">The same question, answered by a plain RAG pipeline.</p>}
    </Card>
  );
}
