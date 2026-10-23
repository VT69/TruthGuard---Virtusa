"use client";

import { pct, USD_TO_INR } from "@/lib/format";
import type { ConfidenceBreakdown, Usage } from "@/lib/types";
import { Card, SectionTitle } from "./ui";

function Gauge({ value }: { value: number }) {
  const angle = Math.PI * (1 - value);
  const x = 60 + 48 * Math.cos(angle);
  const y = 60 - 48 * Math.sin(angle);
  const color = value >= 0.75 ? "#059669" : value >= 0.5 ? "#d97706" : "#e11d48";
  return (
    <svg viewBox="0 0 120 70" className="h-20 w-36" role="img" aria-label={`Confidence ${pct(value)}`}>
      <path d="M12 60 A48 48 0 0 1 108 60" fill="none" stroke="currentColor" strokeOpacity="0.12" strokeWidth="10" strokeLinecap="round" />
      <path
        d={`M12 60 A48 48 0 0 1 ${x.toFixed(2)} ${y.toFixed(2)}`}
        fill="none"
        stroke={color}
        strokeWidth="10"
        strokeLinecap="round"
      />
      <text x="60" y="58" textAnchor="middle" className="fill-current text-[18px] font-semibold">
        {pct(value)}
      </text>
    </svg>
  );
}

function Bar({ label, value, negative }: { label: string; value: number; negative?: boolean }) {
  return (
    <div className="grid grid-cols-[8.5rem_1fr_2.5rem] items-center gap-2 text-xs">
      <span className="text-slate-500">{label}</span>
      <span className="h-1.5 overflow-hidden rounded bg-slate-100 dark:bg-slate-800">
        <span
          className={negative ? "block h-full bg-rose-500" : "block h-full bg-blue-500"}
          style={{ width: `${Math.min(100, Math.round(value * 100))}%` }}
        />
      </span>
      <span className="text-right tabular-nums">{negative && value > 0 ? "−" : ""}{pct(value)}</span>
    </div>
  );
}

export function TrustMeter({ confidence, usage, revisions }: { confidence: ConfidenceBreakdown | null; usage: Usage; revisions: number }) {
  return (
    <Card className="p-4" data-testid="trust-meter">
      <SectionTitle>Trust meter</SectionTitle>
      {confidence ? (
        <div className="flex flex-wrap items-center gap-4">
          <Gauge value={confidence.score} />
          <div className="min-w-[14rem] flex-1 space-y-1.5">
            <Bar label="Retrieval coverage" value={confidence.retrieval_coverage} />
            <Bar label="Weakest entailment" value={confidence.weakest_entailment} />
            <Bar label="Resolution strength" value={confidence.resolution_strength} />
            <Bar label="Inferred metadata" value={confidence.metadata_penalty} negative />
            <Bar label="Open objections" value={confidence.objection_penalty} negative />
          </div>
        </div>
      ) : (
        <p className="text-sm text-slate-500">No confidence for an abstention.</p>
      )}
      <SectionTitle>
        <span className="mt-3 block">Cost ticker</span>
      </SectionTitle>
      <dl className="grid grid-cols-2 gap-2 text-sm sm:grid-cols-4" data-testid="cost-ticker">
        <div>
          <dt className="text-xs text-slate-500">Latency</dt>
          <dd className="font-semibold tabular-nums">{Math.round(usage.latency_ms)} ms</dd>
        </div>
        <div>
          <dt className="text-xs text-slate-500">Tokens</dt>
          <dd className="font-semibold tabular-nums">{(usage.prompt_tokens + usage.completion_tokens).toLocaleString()}</dd>
        </div>
        <div>
          <dt className="text-xs text-slate-500">Cost</dt>
          <dd className="font-semibold tabular-nums">
            ${usage.cost_usd.toFixed(4)} · ₹{(usage.cost_usd * USD_TO_INR).toFixed(3)}
          </dd>
        </div>
        <div>
          <dt className="text-xs text-slate-500">LLM calls</dt>
          <dd className="font-semibold tabular-nums">
            {usage.llm_calls}
            <span className="text-xs font-normal text-slate-500"> ({usage.cache_hits} cached · {revisions} rev.)</span>
          </dd>
        </div>
      </dl>
    </Card>
  );
}
