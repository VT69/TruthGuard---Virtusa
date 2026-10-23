"use client";

import { useQuery } from "@tanstack/react-query";
import clsx from "clsx";
import { useState } from "react";
import { Badge, Card, ErrorNote, SectionTitle, Spinner } from "@/components/ui";
import { XRayDrawer, type XRayTarget } from "@/components/XRayDrawer";
import { get } from "@/lib/api";
import type { Meta } from "@/lib/types";
import { fmtDate, humanize } from "@/lib/format";
import type { LineageSeries } from "@/lib/types";

const DAY = 86_400_000;

function Timeline({ s, onOpen, today }: { s: LineageSeries; onOpen: (t: XRayTarget) => void; today: string }) {
  const now = new Date(`${today}T00:00:00Z`).getTime();
  const dated = s.documents.filter((d) => d.effective_from || d.published_at);
  const undated = s.documents.filter((d) => !(d.effective_from || d.published_at));
  const times = dated.map((d) => new Date(`${d.effective_from ?? d.published_at}T00:00:00Z`).getTime());
  const min = Math.min(...times, now) - 60 * DAY;
  const max = Math.max(...times, now) + 60 * DAY;
  const x = (iso: string) => ((new Date(`${iso}T00:00:00Z`).getTime() - min) / (max - min)) * 100;
  const todayX = ((now - min) / (max - min)) * 100;
  const pos: Record<string, number> = {};
  dated.forEach((d) => (pos[d.id] = x(d.effective_from ?? d.published_at!)));
  undated.forEach((d, i) => (pos[d.id] = 2 + i * 10));
  const titleOf = (id: string) => s.documents.find((d) => d.id === id);
  const short = (id: string) => {
    const d = titleOf(id);
    if (!d) return id;
    return d.title === s.title && d.version ? d.version : d.title;
  };

  return (
    <Card className="p-4" data-testid="lineage-series">
      <SectionTitle right={s.topic && <Badge tone="slate">{humanize(s.topic)}</Badge>}>{s.title}</SectionTitle>
      <div className="relative h-28 overflow-hidden">
        <div className="absolute inset-x-0 top-14 h-px bg-slate-300 dark:bg-slate-700" />
        <div className="absolute top-2 bottom-2 w-px bg-blue-500/60" style={{ left: `${todayX}%` }} title="today">
          <span className="absolute -top-0.5 left-1 text-[10px] text-blue-600">today</span>
        </div>
        {s.documents.map((d, i) => {
          const left = Math.min(98, Math.max(2, pos[d.id]));
          const inactive = d.status !== "active";
          const anchor = left < 12 ? "translate-x-0" : left > 88 ? "-translate-x-full" : "-translate-x-1/2";
          return (
            <button
              key={d.id}
              onClick={() => onOpen({ documentId: d.id })}
              className={clsx(
                "absolute rounded-md border px-1.5 py-1 text-left text-[10px] leading-tight shadow-sm",
                anchor,
                i % 2 ? "top-16" : "top-1",
                inactive
                  ? "border-slate-200 bg-slate-100 text-slate-400 line-through dark:border-slate-800 dark:bg-slate-800"
                  : "border-blue-300 bg-white text-slate-800 dark:border-blue-800 dark:bg-slate-900 dark:text-slate-100",
              )}
              style={{ left: `${left}%`, maxWidth: "11rem" }}
              title={`${d.title} ${d.version ?? ""} — ${d.status}`}
            >
              <span className="block truncate font-semibold">{short(d.id)}</span>
              <span className="block">
                {d.effective_from || d.published_at ? fmtDate(d.effective_from ?? d.published_at) : "undated"}
                {d.inferred_dates && " *"}
              </span>
            </button>
          );
        })}
      </div>
      <ul className="mt-1 space-y-0.5 text-xs text-slate-600 dark:text-slate-300">
        {s.edges.map((e) => (
          <li key={e.id}>
            <b>{short(e.from_doc)}</b> <Badge tone={e.relation === "clarifies" ? "sky" : "amber"}>{e.relation}</Badge>{" "}
            {e.scope_section && <>clause {e.scope_section} of </>}
            <b>{short(e.to_doc)}</b>
            <span className="text-slate-400">
              {" "}
              · {e.provenance} · confidence {Math.round(e.confidence * 100)}%
            </span>
          </li>
        ))}
      </ul>
    </Card>
  );
}

export default function LineagePage() {
  const q = useQuery({ queryKey: ["lineage"], queryFn: () => get<LineageSeries[]>("/v1/lineage") });
  const meta = useQuery({ queryKey: ["meta"], queryFn: () => get<Meta>("/v1/meta") });
  const today = meta.data?.today ?? new Date().toISOString().slice(0, 10);
  const [xray, setXray] = useState<XRayTarget | null>(null);
  if (q.isLoading) return <Spinner />;
  if (q.error) return <ErrorNote error={q.error} />;
  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-xl font-semibold">Lineage timeline</h1>
        <p className="text-sm text-slate-500">
          Versions of each document series on a time axis. Superseded and not-yet-effective versions are greyed out;
          edges come from metadata or from phrases like “this circular supersedes…”. * = dates inferred from text.
        </p>
      </div>
      <div className="grid gap-4 lg:grid-cols-2">
        {q.data?.map((s) => <Timeline key={s.series_id} s={s} onOpen={setXray} today={today} />)}
      </div>
      <XRayDrawer target={xray} onClose={() => setXray(null)} />
    </div>
  );
}
