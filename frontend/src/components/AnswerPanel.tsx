"use client";

import clsx from "clsx";
import { ShieldAlert, ShieldCheck } from "lucide-react";
import { useState } from "react";
import { STATUS_META, TONE } from "@/lib/format";
import type { AnswerClaim, Citation, FinalAnswer } from "@/lib/types";
import { Badge, Card, StatusBadge } from "./ui";

const VERDICT_LABEL: Record<AnswerClaim["verdict"], string> = {
  winner: "applies",
  position: "disputed position",
  superseded: "set aside",
  info: "note",
  corroborated: "corroborates",
};

export function AnswerPanel({
  answer,
  travelling,
  onCite,
}: {
  answer: FinalAnswer;
  travelling: boolean;
  onCite: (c: Citation) => void;
}) {
  const [showAll, setShowAll] = useState(false);
  const setAside = answer.claims.filter((c) => c.verdict === "superseded");
  const visible = showAll ? answer.claims : answer.claims.filter((c) => c.verdict !== "superseded");
  const meta = STATUS_META[answer.status];
  const tone = TONE[meta.tone];
  return (
    <Card className={clsx("overflow-hidden", travelling && "ring-2 ring-blue-400")} data-testid="answer-panel">
      <div className={clsx("flex flex-wrap items-center gap-2 border-b px-4 py-3", tone.soft, tone.border)}>
        <StatusBadge status={answer.status} large />
        <span className={clsx("text-sm", tone.text)}>{meta.blurb}</span>
        <span className="ml-auto text-xs text-slate-500">
          as of <b>{answer.as_of}</b>
          {travelling && " (time travel)"}
        </span>
      </div>
      <div className="space-y-4 px-4 py-4">
        <p className="text-[15px] leading-relaxed" data-testid="answer-text">
          {answer.answer_text}
        </p>

        {answer.injection_flags.length > 0 && (
          <div className="flex items-start gap-2 rounded-lg border border-rose-300 bg-rose-50 px-3 py-2 text-xs text-rose-800 dark:border-rose-800 dark:bg-rose-950/40 dark:text-rose-200" data-testid="injection-badge">
            <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
            <span>
              <b>Prompt injection quarantined.</b> {answer.injection_flags.join(", ")} contains instructions aimed at AI
              assistants. Its text was treated as data and its claims were excluded.
            </span>
          </div>
        )}

        {answer.claims.length > 0 && (
          <ul className="space-y-2" aria-label="Claims">
            {visible.map((c) => (
              <li key={c.id} className="rounded-lg border border-slate-100 px-3 py-2 dark:border-slate-800" data-testid="claim">
                <div className="flex flex-wrap items-start gap-2">
                  <Badge
                    tone={c.verdict === "winner" ? "emerald" : c.verdict === "position" ? "rose" : c.verdict === "superseded" ? "slate" : "sky"}
                  >
                    {VERDICT_LABEL[c.verdict]}
                  </Badge>
                  <span className={clsx("flex-1 text-sm", c.verdict === "superseded" && "text-slate-500")}>{c.text}</span>
                </div>
                <div className="mt-1.5 flex flex-wrap gap-1.5">
                  {c.citations.map((cit, i) => (
                    <button
                      key={`${cit.chunk_id}-${i}`}
                      onClick={() => onCite(cit)}
                      className={clsx(
                        "inline-flex max-w-full items-center gap-1 truncate rounded-md border px-2 py-0.5 text-left text-[11px] hover:bg-slate-50 dark:hover:bg-slate-800",
                        cit.verified === false ? "border-rose-300 text-rose-700" : "border-slate-200 text-slate-600 dark:border-slate-700 dark:text-slate-300",
                      )}
                      title={`Open the source at the quoted span: “${cit.quote}”`}
                      data-testid="citation"
                    >
                      {cit.verified ? <ShieldCheck className="h-3 w-3 shrink-0 text-emerald-600" aria-label="quote verified" /> : null}
                      <span className="truncate">{cit.document_id}</span>
                    </button>
                  ))}
                  {c.entailment !== null && (
                    <span className="text-[11px] text-slate-400">entailment {Math.round((c.entailment ?? 0) * 100)}%</span>
                  )}
                </div>
              </li>
            ))}
          </ul>
        )}
        {setAside.length > 0 && (
          <button onClick={() => setShowAll((s) => !s)} className="text-xs font-medium text-blue-700 hover:underline dark:text-blue-300">
            {showAll ? "Hide" : "Show"} {setAside.length} set-aside source claim{setAside.length > 1 ? "s" : ""} with citations
          </button>
        )}

        {answer.missing_evidence.length > 0 && (
          <div className="rounded-lg bg-slate-50 px-3 py-2 text-sm text-slate-600 dark:bg-slate-800/60 dark:text-slate-300">
            <b>Missing evidence:</b> {answer.missing_evidence.join(" ")}
          </div>
        )}
        {answer.residual_objections.length > 0 && (
          <div className="text-xs text-amber-700 dark:text-amber-300">
            Residual critic objections: {answer.residual_objections.map((o) => o.type).join(", ")}
          </div>
        )}
      </div>
    </Card>
  );
}
