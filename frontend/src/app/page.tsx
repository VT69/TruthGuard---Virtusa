"use client";

import { useQuery } from "@tanstack/react-query";
import { Search, Swords } from "lucide-react";
import { useCallback, useRef, useState } from "react";
import { AnswerPanel } from "@/components/AnswerPanel";
import { BaselinePanel } from "@/components/BaselinePanel";
import { ChallengeMode } from "@/components/ChallengeMode";
import { ConflictCard } from "@/components/ConflictCard";
import { DebateTheatre } from "@/components/DebateTheatre";
import { TimeTravel } from "@/components/TimeTravel";
import { TrustMeter } from "@/components/TrustMeter";
import { Button, Card, ErrorNote, Spinner } from "@/components/ui";
import { XRayDrawer, type XRayTarget } from "@/components/XRayDrawer";
import { get, reResolve, streamQuery } from "@/lib/api";
import { OUTCOME_LABEL } from "@/components/ConflictCard";
import type { BaselineAnswer, FinalAnswer, Meta, Position, TraceEvent } from "@/lib/types";

const EXAMPLES = [
  "What is the late payment fee on the Platinum Credit Card?",
  "How many days do customers have to report a disputed transaction?",
  "How often must high-risk customers update their KYC?",
  "What is the interest rate on a 1-year fixed deposit?",
  "What is the minimum income required for a personal loan?",
  "What is the replacement fee for a lost debit card?",
  "What is the annual fee for the Titanium Credit Card?",
];

export default function AskPage() {
  const meta = useQuery({ queryKey: ["meta"], queryFn: () => get<Meta>("/v1/meta"), retry: 0 });
  const [question, setQuestion] = useState(EXAMPLES[0]);
  const [asOf, setAsOf] = useState("");
  const [compare, setCompare] = useState(true);
  const [baselineMode, setBaselineMode] = useState<"B0" | "B1" | "B2">("B0");
  const [events, setEvents] = useState<TraceEvent[]>([]);
  const [answer, setAnswer] = useState<FinalAnswer | null>(null);
  const [baseline, setBaseline] = useState<BaselineAnswer | null>(null);
  const [travel, setTravel] = useState<FinalAnswer | null>(null);
  const [travelBusy, setTravelBusy] = useState(false);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [xray, setXray] = useState<XRayTarget | null>(null);
  const [challenge, setChallenge] = useState(false);
  const abort = useRef<AbortController | null>(null);

  const ask = useCallback(
    async (q: string, mode = baselineMode) => {
      abort.current?.abort();
      const ctl = new AbortController();
      abort.current = ctl;
      setRunning(true);
      setError(null);
      setEvents([]);
      setAnswer(null);
      setTravel(null);
      setBaseline(null);
      try {
        const tg = streamQuery({ question: q, as_of: asOf || null }, (ev) => setEvents((xs) => [...xs, ev]), ctl.signal);
        const b = compare
          ? streamQuery({ question: q, as_of: asOf || null, mode }, () => undefined, ctl.signal).then((r) =>
              setBaseline(r as BaselineAnswer),
            )
          : Promise.resolve();
        const [final] = await Promise.all([tg, b]);
        setAnswer(final as FinalAnswer);
      } catch (e) {
        if ((e as Error).name !== "AbortError") setError(e);
      } finally {
        setRunning(false);
      }
    },
    [asOf, compare, baselineMode],
  );

  const onTravel = async (date: string | null) => {
    if (!answer) return;
    if (!date) {
      setTravel(null);
      return;
    }
    setTravelBusy(true);
    try {
      setTravel(await reResolve(answer.trace_id, { as_of: date }));
    } catch (e) {
      setError(e);
    } finally {
      setTravelBusy(false);
    }
  };

  const shown = travel ?? answer;
  const openPosition = (p: Position) =>
    setXray({
      documentId: p.document_id,
      quote: p.quote,
      stamp: p.outcome === "eliminated" && p.eliminated_by ? `${OUTCOME_LABEL[p.eliminated_by]} (${p.eliminated_by})` : null,
    });

  return (
    <div className="space-y-4">
      <Card className="p-4">
        <form
          className="flex flex-col gap-3 lg:flex-row lg:items-end"
          onSubmit={(e) => {
            e.preventDefault();
            void ask(question);
          }}
        >
          <label className="flex-1">
            <span className="mb-1 block text-xs font-semibold uppercase tracking-wide text-slate-500">
              Ask Northwind Bank&apos;s knowledge base
            </span>
            <div className="relative">
              <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" aria-hidden />
              <input
                value={question}
                onChange={(e) => setQuestion(e.target.value)}
                className="w-full rounded-lg border border-slate-300 bg-white py-2.5 pl-9 pr-3 text-[15px] dark:border-slate-700 dark:bg-slate-950"
                placeholder="e.g. What is the late fee on the Platinum card?"
                aria-label="Question"
                data-testid="question-input"
              />
            </div>
          </label>
          <label className="w-full lg:w-44">
            <span className="mb-1 block text-xs font-semibold uppercase tracking-wide text-slate-500">As of (optional)</span>
            <input
              type="date"
              value={asOf}
              onChange={(e) => setAsOf(e.target.value)}
              className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2 dark:border-slate-700 dark:bg-slate-950"
              aria-label="As-of date"
            />
          </label>
          <label className="flex items-center gap-2 text-sm lg:pb-2.5">
            <input type="checkbox" checked={compare} onChange={(e) => setCompare(e.target.checked)} className="h-4 w-4 accent-blue-600" />
            Compare with naive RAG
          </label>
          <div className="flex gap-2">
            <Button type="submit" disabled={running || question.trim().length < 3} className="px-4 py-2.5" data-testid="ask-button">
              {running ? <Spinner /> : null} Ask
            </Button>
            <Button type="button" variant="outline" onClick={() => setChallenge(true)} className="py-2.5" data-testid="challenge-open">
              <Swords className="h-4 w-4 text-rose-600" aria-hidden /> Challenge
            </Button>
          </div>
        </form>
        <div className="mt-3 flex flex-wrap gap-1.5" aria-label="Example questions">
          {EXAMPLES.map((q) => (
            <button
              key={q}
              onClick={() => {
                setQuestion(q);
                void ask(q);
              }}
              className="rounded-full border border-slate-200 px-2.5 py-1 text-xs text-slate-600 hover:border-blue-400 hover:text-blue-700 dark:border-slate-700 dark:text-slate-300"
            >
              {q}
            </button>
          ))}
        </div>
      </Card>

      <ErrorNote error={error} />

      <DebateTheatre events={events} running={running} />

      <div className={compare ? "grid gap-4 xl:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)]" : "grid gap-4"}>
        <div className="min-w-0 space-y-4">
          {shown ? (
            <>
              <AnswerPanel
                answer={shown}
                travelling={!!travel}
                onCite={(c) =>
                  setXray({ documentId: c.document_id, quote: c.quote, start: c.char_start, end: c.char_end })
                }
              />
              {shown.conflicts.map((c) => (
                <ConflictCard key={`${c.conflict_id}-${shown.as_of}`} conflict={c} onXRay={openPosition} />
              ))}
            </>
          ) : running ? (
            <Card className="flex items-center gap-2 p-6 text-sm text-slate-500">
              <Spinner /> TruthGuard is planning, retrieving, resolving and cross-examining…
            </Card>
          ) : (
            <Card className="p-6 text-sm text-slate-500">
              Ask a question, or pick an example. Every answer ends in one of four states: verified, conflict
              resolved, unresolved conflict, or insufficient evidence.
            </Card>
          )}
        </div>
        {compare && (
          <div className="min-w-0 space-y-4">
            <BaselinePanel
              answer={baseline}
              running={running}
              mode={baselineMode}
              onMode={(m) => {
                setBaselineMode(m);
                if (answer) void ask(answer.question, m);
              }}
            />
          </div>
        )}
      </div>

      {answer && (
        <div className="grid gap-4 lg:grid-cols-2">
          <TimeTravel original={answer} onTravel={onTravel} busy={travelBusy} />
          {shown && <TrustMeter confidence={shown.confidence} usage={answer.usage} revisions={answer.revisions} />}
        </div>
      )}

      <XRayDrawer target={xray} onClose={() => setXray(null)} />
      <ChallengeMode
        open={challenge}
        onClose={() => setChallenge(false)}
        onReask={() => void ask(question)}
        today={meta.data?.today ?? new Date().toISOString().slice(0, 10)}
      />
    </div>
  );
}
