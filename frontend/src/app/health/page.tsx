"use client";

import { useQuery } from "@tanstack/react-query";
import clsx from "clsx";
import { Download, FileWarning, ShieldAlert } from "lucide-react";
import { useMemo, useState } from "react";
import { outcomeBadge } from "@/components/ConflictCard";
import { Badge, Card, ErrorNote, SectionTitle, Spinner } from "@/components/ui";
import { XRayDrawer, type XRayTarget } from "@/components/XRayDrawer";
import { API_URL, get } from "@/lib/api";
import { humanize, RULE_NAMES, SEVERITY_TONE } from "@/lib/format";
import type { Health, HealthConflict, RuleId } from "@/lib/types";

const CELL: Record<string, string> = {
  critical: "bg-rose-500 hover:bg-rose-600",
  major: "bg-amber-400 hover:bg-amber-500",
  minor: "bg-sky-300 hover:bg-sky-400",
};

function Stat({ label, value, tone }: { label: string; value: number | string; tone?: string }) {
  return (
    <Card className="p-4">
      <div className="text-xs uppercase tracking-wide text-slate-500">{label}</div>
      <div className={clsx("mt-1 text-2xl font-semibold tabular-nums", tone)}>{value}</div>
    </Card>
  );
}

export default function HealthPage() {
  const q = useQuery({ queryKey: ["health"], queryFn: () => get<Health>("/v1/conflicts") });
  const [selected, setSelected] = useState<string | null>(null);
  const [topic, setTopic] = useState<string | null>(null);
  const [xray, setXray] = useState<XRayTarget | null>(null);
  const h = q.data;
  const conflicts = useMemo(
    () =>
      (h?.conflicts ?? []).filter(
        (c) => (!topic || c.topic === topic) && (!selected || c.positions.some((p) => p.document_id === selected)),
      ),
    [h, topic, selected],
  );

  if (q.isLoading) return <Spinner />;
  if (q.error || !h) return <ErrorNote error={q.error ?? "no data"} />;

  const cellFor = (t: string, d: string) => h.heatmap.cells.find((c) => c.topic === t && c.document_id === d);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-2">
        <div>
          <h1 className="text-xl font-semibold">Corpus Health</h1>
          <p className="text-sm text-slate-500">
            Every contradiction in the knowledge base, found before a customer hits it. As of {h.as_of}.
          </p>
        </div>
        <a
          href={`${API_URL}/v1/conflicts/report.md`}
          className="inline-flex items-center gap-1.5 rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-sm font-medium hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-900"
          data-testid="download-report"
        >
          <Download className="h-4 w-4" aria-hidden /> Download contradiction report
        </a>
      </div>

      <div className="grid grid-cols-2 gap-3 md:grid-cols-5" data-testid="health-totals">
        <Stat label="Contradictions" value={h.totals.conflicts} />
        <Stat label="Unresolved" value={h.totals.unresolved} tone="text-rose-600" />
        <Stat label="Critical" value={h.totals.by_severity.critical ?? 0} tone="text-rose-600" />
        <Stat label="Stale docs still live" value={h.totals.stale_documents} tone="text-amber-600" />
        <Stat label="Quarantined" value={h.totals.quarantined_documents} />
      </div>

      <Card className="p-4">
        <SectionTitle
          right={
            <span className="flex items-center gap-2 text-xs text-slate-500">
              {["critical", "major", "minor"].map((s) => (
                <span key={s} className="inline-flex items-center gap-1">
                  <span className={clsx("h-3 w-3 rounded-sm", CELL[s])} /> {s}
                </span>
              ))}
              {(topic || selected) && (
                <button className="ml-2 text-blue-600 underline" onClick={() => (setTopic(null), setSelected(null))}>
                  clear filter
                </button>
              )}
            </span>
          }
        >
          Heatmap: topic × document
        </SectionTitle>
        <div className="space-y-2 overflow-x-auto" data-testid="heatmap">
          {h.heatmap.topics.map((t) => {
            const docs = h.heatmap.documents[t] ?? [];
            return (
              <div key={t} className="flex items-center gap-2">
                <button
                  onClick={() => setTopic(topic === t ? null : t)}
                  className={clsx("w-32 shrink-0 text-left text-xs font-medium", topic === t && "text-blue-600")}
                >
                  {humanize(t)}
                </button>
                <div className="flex flex-wrap gap-1">
                  {docs.map((d) => {
                    const cell = cellFor(t, d.id);
                    return (
                      <button
                        key={d.id}
                        onClick={() => setSelected(selected === d.id ? null : d.id)}
                        title={`${d.title}${d.version ? ` ${d.version}` : ""} — ${cell ? cell.severity : "no conflicts"}`}
                        aria-label={`${d.title} ${cell ? cell.severity : "no conflicts"}`}
                        className={clsx(
                          "h-7 w-7 rounded-md border transition",
                          cell ? CELL[cell.severity] : "border-slate-200 bg-slate-100 dark:border-slate-800 dark:bg-slate-800",
                          selected === d.id && "ring-2 ring-blue-600 ring-offset-1",
                        )}
                      />
                    );
                  })}
                </div>
              </div>
            );
          })}
        </div>
      </Card>

      <div className="grid gap-4 xl:grid-cols-[minmax(0,1.7fr)_minmax(0,1fr)]">
        <div className="space-y-3">
          <SectionTitle>Contradictions ({conflicts.length})</SectionTitle>
          {conflicts.map((c) => (
            <HealthConflictCard key={c.id} c={c} onOpen={setXray} />
          ))}
        </div>
        <div className="space-y-4">
          <Card className="p-4">
            <SectionTitle>Recommended actions</SectionTitle>
            <ul className="space-y-2 text-sm" data-testid="suggestions">
              {h.suggestions.map((s, i) => (
                <li key={i} className="flex gap-2">
                  {s.action === "escalate" ? (
                    <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0 text-rose-600" aria-hidden />
                  ) : (
                    <FileWarning className="mt-0.5 h-4 w-4 shrink-0 text-amber-600" aria-hidden />
                  )}
                  <span>{s.text}</span>
                </li>
              ))}
            </ul>
          </Card>
          <Card className="p-4">
            <SectionTitle>Stale documents still live</SectionTitle>
            <ul className="space-y-1.5 text-sm">
              {h.stale_documents.map((d) => (
                <li key={d.document_id} className="flex flex-wrap items-center gap-1.5">
                  <button className="font-medium hover:underline" onClick={() => setXray({ documentId: d.document_id })}>
                    {d.title} {d.version && !d.title.includes(d.version) ? d.version : ""}
                  </button>
                  <Badge tone="slate">{humanize(d.doc_type)}</Badge>
                  {d.rules.map((r) => (
                    <Badge key={r} tone="amber" title={RULE_NAMES[r as RuleId]}>
                      loses by {r}
                    </Badge>
                  ))}
                </li>
              ))}
            </ul>
          </Card>
        </div>
      </div>
      <XRayDrawer target={xray} onClose={() => setXray(null)} />
    </div>
  );
}

function HealthConflictCard({ c, onOpen }: { c: HealthConflict; onOpen: (t: XRayTarget) => void }) {
  return (
    <Card className="p-3" data-testid="health-conflict">
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="text-sm font-semibold">{c.label}</span>
        <Badge tone={SEVERITY_TONE[c.severity]}>{c.severity}</Badge>
        <Badge tone="slate">{humanize(c.type.toLowerCase())}</Badge>
        <Badge tone={c.status === "unresolved" ? "rose" : "emerald"}>
          {c.status === "unresolved" ? "unresolved" : `${c.deciding_rule} ${RULE_NAMES[c.deciding_rule as RuleId] ?? ""}`}
        </Badge>
        {c.flags.includes("possible_unofficial_update") && <Badge tone="rose">possible unofficial update</Badge>}
      </div>
      <ul className="mt-2 space-y-1">
        {c.positions.map((p) => (
          <li key={p.assertion_id} className="flex flex-wrap items-center gap-2 text-xs">
            <span className={clsx("w-20 font-semibold tabular-nums", p.outcome === "eliminated" && "text-slate-400 line-through")}>
              {p.value}
            </span>
            <button className="font-medium hover:underline" onClick={() => onOpen({ documentId: p.document_id, quote: p.quote })}>
              {p.title}
            </button>
            <span className="text-slate-500">
              tier {p.tier} · {p.date ?? "undated"}
            </span>
            {outcomeBadge({ outcome: p.outcome as never, eliminated_by: p.eliminated_by as RuleId | null })}
          </li>
        ))}
      </ul>
      {c.owner && c.status === "unresolved" && <p className="mt-1 text-xs text-rose-600">Owner to decide: {c.owner}</p>}
    </Card>
  );
}
