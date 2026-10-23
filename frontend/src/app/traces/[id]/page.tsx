"use client";

import { useQuery } from "@tanstack/react-query";
import { Pause, Play } from "lucide-react";
import { use, useEffect, useState } from "react";
import { DebateTheatre } from "@/components/DebateTheatre";
import { Button, Card, ErrorNote, SectionTitle, Spinner, StatusBadge } from "@/components/ui";
import { get } from "@/lib/api";
import type { FinalAnswer, TraceEvent } from "@/lib/types";

interface Trace {
  id: string;
  question: string;
  as_of: string;
  status: FinalAnswer["status"];
  final: FinalAnswer;
  events: TraceEvent[];
  latency_ms: number;
}

export default function TraceReplay({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const q = useQuery({ queryKey: ["trace", id], queryFn: () => get<Trace>(`/v1/traces/${id}`) });
  const [step, setStep] = useState<number | null>(null);
  const [playing, setPlaying] = useState(false);
  const events = q.data?.events ?? [];
  const shown = step === null ? events : events.slice(0, step);

  useEffect(() => {
    if (!playing) return;
    if (step !== null && step >= events.length) {
      setPlaying(false);
      return;
    }
    const t = setTimeout(() => setStep((s) => (s ?? 0) + 1), 450);
    return () => clearTimeout(t);
  }, [playing, step, events.length]);

  if (q.isLoading) return <Spinner />;
  if (q.error || !q.data) return <ErrorNote error={q.error ?? "not found"} />;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <StatusBadge status={q.data.status} large />
        <h1 className="text-lg font-semibold">{q.data.question}</h1>
        <span className="text-xs text-slate-500">
          {q.data.id} · as of {q.data.as_of} · {Math.round(q.data.latency_ms)} ms
        </span>
        <Button
          className="ml-auto"
          onClick={() => {
            if (!playing && (step === null || step >= events.length)) setStep(0);
            setPlaying((p) => !p);
          }}
          data-testid="replay"
        >
          {playing ? <Pause className="h-4 w-4" /> : <Play className="h-4 w-4" />} {playing ? "Pause" : "Replay debate"}
        </Button>
      </div>
      <DebateTheatre events={shown} running={playing} />
      <Card className="p-4">
        <SectionTitle>Event log</SectionTitle>
        <ol className="space-y-1 font-mono text-xs">
          {shown.map((e) => (
            <li key={e.seq} className="flex flex-wrap gap-2">
              <span className="w-6 text-right text-slate-400">{e.seq}</span>
              <span className="w-40 font-semibold">{e.event}</span>
              <span className="w-24 text-slate-500">{e.node}</span>
              <span className="tabular-nums text-slate-500">
                {e.latency_ms} ms · {e.tokens} tok · ${e.cost_usd.toFixed(5)}
              </span>
            </li>
          ))}
        </ol>
      </Card>
      <Card className="p-4">
        <SectionTitle>Final answer</SectionTitle>
        <p className="text-sm">{q.data.final.answer_text}</p>
      </Card>
    </div>
  );
}
