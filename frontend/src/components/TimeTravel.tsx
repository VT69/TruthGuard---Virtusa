"use client";

import { CalendarClock, Undo2 } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { fmtDate } from "@/lib/format";
import type { FinalAnswer } from "@/lib/types";
import { Button, Card, SectionTitle, Spinner } from "./ui";

const DAY = 86_400_000;
const toDays = (iso: string) => Math.round(new Date(`${iso}T00:00:00Z`).getTime() / DAY);
const toIso = (days: number) => new Date(days * DAY).toISOString().slice(0, 10);

/** Drag the as-of date: the server re-runs ONLY the resolver + writer on the fixed evidence. */
export function TimeTravel({
  original,
  onTravel,
  busy,
}: {
  original: FinalAnswer;
  onTravel: (asOf: string | null) => void;
  busy: boolean;
}) {
  const marks = useMemo(() => {
    const out = new Map<string, string>();
    for (const c of original.conflicts)
      for (const p of c.positions) {
        const d = p.effective_from ?? p.published_at;
        if (d) out.set(d, `${p.document_title}${p.version_label ? ` ${p.version_label}` : ""}`);
      }
    return [...out.entries()].sort(([a], [b]) => a.localeCompare(b));
  }, [original]);
  const base = toDays(original.as_of);
  const min = Math.min(base, ...marks.map(([d]) => toDays(d))) - 90;
  const max = Math.max(base, ...marks.map(([d]) => toDays(d))) + 180;
  const [value, setValue] = useState(base);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => setValue(base), [base, original.trace_id]);

  const change = (v: number) => {
    setValue(v);
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => onTravel(v === base ? null : toIso(v)), 120);
  };

  return (
    <Card className="p-4" data-testid="time-travel">
      <SectionTitle
        right={
          value !== base && (
            <Button variant="ghost" onClick={() => change(base)} className="text-xs">
              <Undo2 className="h-3.5 w-3.5" /> back to {fmtDate(original.as_of)}
            </Button>
          )
        }
      >
        <span className="inline-flex items-center gap-1.5">
          <CalendarClock className="h-3.5 w-3.5" aria-hidden /> Time travel
        </span>
      </SectionTitle>
      <div className="flex items-center gap-3">
        <span className="w-28 text-sm font-semibold tabular-nums" aria-live="polite">
          as of {fmtDate(toIso(value))}
        </span>
        {busy && <Spinner className="text-blue-600" />}
      </div>
      <div className="relative mt-2 pb-12">
        <input
          type="range"
          min={min}
          max={max}
          step={1}
          value={value}
          onChange={(e) => change(Number(e.target.value))}
          aria-label="As-of date"
          aria-valuetext={toIso(value)}
          className="w-full accent-blue-600"
          data-testid="time-slider"
        />
        {marks.map(([d, label], i) => {
          const left = ((toDays(d) - min) / (max - min)) * 100;
          return (
            <button
              key={d}
              onClick={() => change(toDays(d))}
              title={`${label} · ${d}`}
              className="absolute -translate-x-1/2 text-[10px] text-slate-500 hover:text-blue-600"
              style={{ left: `${left}%`, top: "1.5rem" }}
            >
              <span className={`mx-auto mb-0.5 block w-0.5 bg-slate-400 ${i % 2 ? "h-5" : "h-2"}`} />
              {d.slice(0, 7)}
            </button>
          );
        })}
      </div>
      <p className="text-xs text-slate-500">
        Evidence stays fixed; only the deterministic resolver re-runs (no LLM call), so each move takes milliseconds.
      </p>
    </Card>
  );
}
