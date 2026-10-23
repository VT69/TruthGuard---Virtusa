"use client";

import { useQuery } from "@tanstack/react-query";
import clsx from "clsx";
import { Card, Empty, SectionTitle, Spinner } from "@/components/ui";
import { ApiError, get } from "@/lib/api";
import { pct } from "@/lib/format";

interface Agg {
  silent_blend_rate: number | null;
  conflict_detection: { f1: number };
  resolution_accuracy: number | null;
  status_accuracy: number | null;
  value_accuracy: number | null;
  stale_citation_rate: number | null;
  abstention_recall: number | null;
  latency_ms: { p50: number; p95: number };
  cost_usd_per_query: number;
}
interface Report {
  split: string;
  n_questions: number;
  llm_mode: string;
  generated: string;
  runs: Record<string, Agg | { skipped: string }>;
  descriptions: Record<string, string>;
  retrieval_sweep?: { k: number; lineage_expansion: boolean; winner_recall: number; all_versions_recall: number }[];
}

export default function EvalPage() {
  const q = useQuery({ queryKey: ["eval"], queryFn: () => get<Report>("/v1/eval/results"), retry: 0 });
  if (q.isLoading) return <Spinner />;
  if (q.error)
    return (
      <Empty>
        {q.error instanceof ApiError && q.error.status === 404 ? "No eval results yet: run `make eval`." : String(q.error)}
      </Empty>
    );
  const r = q.data!;
  const runs = Object.entries(r.runs).filter((e): e is [string, Agg] => !("skipped" in e[1]));
  const maxBlend = Math.max(...runs.map(([, a]) => a.silent_blend_rate ?? 0), 0.1);

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-xl font-semibold">Measured proof</h1>
        <p className="text-sm text-slate-500">
          {r.split} split · {r.n_questions} gold questions · LLM mode <b>{r.llm_mode}</b> · generated {r.generated}.
          {r.llm_mode === "offline" && " Baselines are deterministic proxies of naive-RAG behaviour; run with LLM_MODE=live for real LLM baselines."}
        </p>
      </div>
      <Card className="p-4">
        <SectionTitle>Silent-blend rate (conflict questions answered without disclosure) — lower is better</SectionTitle>
        <div className="space-y-2" data-testid="eval-chart">
          {runs.map(([name, a]) => (
            <div key={name} className="grid grid-cols-[4.5rem_1fr_3.5rem] items-center gap-2 text-sm">
              <span className="font-semibold">{name}</span>
              <span className="h-5 overflow-hidden rounded bg-slate-100 dark:bg-slate-800">
                <span
                  className={clsx("block h-full", name.startsWith("TG") ? "bg-blue-600" : name.startsWith("A") ? "bg-slate-400" : "bg-rose-500")}
                  style={{ width: `${((a.silent_blend_rate ?? 0) / maxBlend) * 100}%` }}
                />
              </span>
              <span className="text-right tabular-nums">{pct(a.silent_blend_rate)}</span>
            </div>
          ))}
        </div>
      </Card>
      <Card className="overflow-x-auto p-4">
        <table className="w-full text-sm">
          <thead className="text-left text-xs uppercase text-slate-500">
            <tr>
              {["Run", "Silent blend", "Conflict F1", "Resolution", "Status", "Values", "Stale cites", "Abstention", "p50 ms", "$/query"].map((h) => (
                <th key={h} className="pb-2 pr-3">
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {Object.entries(r.runs).map(([name, a]) =>
              "skipped" in a ? (
                <tr key={name} className="border-t border-slate-100 text-slate-400 dark:border-slate-800">
                  <td className="py-1.5 pr-3 font-semibold">{name}</td>
                  <td colSpan={9}>{a.skipped}</td>
                </tr>
              ) : (
                <tr key={name} className="border-t border-slate-100 tabular-nums dark:border-slate-800" title={r.descriptions[name]}>
                  <td className="py-1.5 pr-3 font-semibold">{name}</td>
                  <td className="pr-3">{pct(a.silent_blend_rate)}</td>
                  <td className="pr-3">{a.conflict_detection.f1.toFixed(2)}</td>
                  <td className="pr-3">{pct(a.resolution_accuracy)}</td>
                  <td className="pr-3">{pct(a.status_accuracy)}</td>
                  <td className="pr-3">{pct(a.value_accuracy)}</td>
                  <td className="pr-3">{pct(a.stale_citation_rate)}</td>
                  <td className="pr-3">{pct(a.abstention_recall)}</td>
                  <td className="pr-3">{a.latency_ms.p50}</td>
                  <td>{a.cost_usd_per_query}</td>
                </tr>
              ),
            )}
          </tbody>
        </table>
        <ul className="mt-3 space-y-0.5 text-xs text-slate-500">
          {Object.entries(r.descriptions).map(([k, v]) => (
            <li key={k}>
              <b>{k}</b>: {v}
            </li>
          ))}
        </ul>
      </Card>
      {r.retrieval_sweep && (
        <Card className="p-4">
          <SectionTitle>Lineage expansion vs retrieval budget</SectionTitle>
          <table className="text-sm">
            <thead className="text-left text-xs uppercase text-slate-500">
              <tr>
                <th className="pr-4">k</th>
                <th className="pr-4">Expansion</th>
                <th className="pr-4">Winner in evidence</th>
                <th>Every version in evidence</th>
              </tr>
            </thead>
            <tbody>
              {r.retrieval_sweep.map((s) => (
                <tr key={`${s.k}-${s.lineage_expansion}`} className="tabular-nums">
                  <td className="pr-4">{s.k}</td>
                  <td className="pr-4">{s.lineage_expansion ? "on" : "off"}</td>
                  <td className="pr-4">{pct(s.winner_recall)}</td>
                  <td>{pct(s.all_versions_recall)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}
    </div>
  );
}
