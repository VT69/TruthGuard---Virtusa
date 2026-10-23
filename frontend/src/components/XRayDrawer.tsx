"use client";

import { useQuery } from "@tanstack/react-query";
import { useEffect, useRef } from "react";
import { get } from "@/lib/api";
import { fmtDate, humanize, tierLabel } from "@/lib/format";
import type { DocumentDetail } from "@/lib/types";
import { Badge, Drawer, ErrorNote, Spinner } from "./ui";

export interface XRayTarget {
  documentId: string;
  quote?: string;
  start?: number | null;
  end?: number | null;
  stamp?: string | null;
}

export function XRayDrawer({ target, onClose }: { target: XRayTarget | null; onClose: () => void }) {
  const q = useQuery({
    queryKey: ["doc", target?.documentId],
    queryFn: () => get<DocumentDetail>(`/v1/documents/${target!.documentId}`),
    enabled: !!target,
  });
  const markRef = useRef<HTMLElement>(null);
  useEffect(() => {
    markRef.current?.scrollIntoView({ block: "center", behavior: "smooth" });
  }, [q.data, target]);

  const d = q.data;
  let start = target?.start ?? null;
  let end = target?.end ?? null;
  if (d && target?.quote && (start === null || end === null)) {
    const i = d.raw_text.indexOf(target.quote);
    if (i >= 0) {
      start = i;
      end = i + target.quote.length;
    }
  }
  const stamp = target?.stamp ?? (d?.status === "superseded" ? "SUPERSEDED" : d?.status === "not_yet_effective" ? "NOT YET IN FORCE" : null);

  return (
    <Drawer
      open={!!target}
      onClose={onClose}
      wide
      title={
        <span className="flex flex-wrap items-center gap-2">
          Evidence X-ray {d && <span className="truncate font-normal text-slate-500">· {d.document.title}</span>}
        </span>
      }
    >
      {q.isLoading && <Spinner />}
      <ErrorNote error={q.error} />
      {d && (
        <div className="space-y-4" data-testid="xray">
          <div className="flex flex-wrap gap-1.5 text-xs">
            <Badge tone="slate">{humanize(d.document.doc_type)}</Badge>
            <Badge tone="slate">
              Tier {d.document.authority_tier} · {tierLabel(d.document.authority_tier)}
            </Badge>
            {d.document.version_label && <Badge tone="slate">{d.document.version_label}</Badge>}
            <Badge tone="slate">effective {fmtDate(d.document.effective_from ?? d.document.published_at)}</Badge>
            <Badge tone={d.status === "active" ? "emerald" : "rose"}>{humanize(d.status)}</Badge>
            {d.document.injection_flag && <Badge tone="rose">Contains quarantined prompt injection</Badge>}
          </div>
          <div className="relative rounded-lg border border-slate-200 bg-slate-50 p-4 dark:border-slate-800 dark:bg-slate-950">
            {stamp && (
              <div
                className="pointer-events-none absolute right-4 top-4 rotate-[-8deg] rounded border-4 border-rose-500/80 px-3 py-1 text-sm font-black uppercase tracking-widest text-rose-600/80"
                data-testid="xray-stamp"
              >
                {stamp}
              </div>
            )}
            <pre className="whitespace-pre-wrap break-words font-sans text-sm leading-relaxed">
              {start !== null && end !== null ? (
                <>
                  {d.raw_text.slice(0, start)}
                  <mark ref={markRef} className="xray">
                    {d.raw_text.slice(start, end)}
                  </mark>
                  {d.raw_text.slice(end)}
                </>
              ) : (
                d.raw_text
              )}
            </pre>
          </div>
          <div>
            <h3 className="mb-1 text-xs font-semibold uppercase text-slate-500">Metadata provenance</h3>
            <table className="w-full text-xs">
              <tbody>
                {Object.entries(d.document.field_provenance).map(([k, v]) => (
                  <tr key={k} className="border-b border-slate-100 dark:border-slate-800">
                    <td className="py-1 pr-2 font-medium">{humanize(k)}</td>
                    <td className="py-1 pr-2">{v.provenance}</td>
                    <td className="py-1 text-right tabular-nums">{Math.round(v.confidence * 100)}%</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {d.edges.length > 0 && (
            <div>
              <h3 className="mb-1 text-xs font-semibold uppercase text-slate-500">Lineage</h3>
              <ul className="space-y-1 text-xs">
                {d.edges.map((e) => (
                  <li key={e.id}>
                    <b>{e.from_doc}</b> {e.relation}
                    {e.scope_section ? ` clause ${e.scope_section} of` : ""} <b>{e.to_doc}</b>{" "}
                    <span className="text-slate-500">
                      ({e.provenance}, {Math.round(e.confidence * 100)}%) — “{e.evidence_span}”
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </Drawer>
  );
}
