"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { Card, Empty, ErrorNote, Spinner, StatusBadge } from "@/components/ui";
import { get } from "@/lib/api";
import type { AnswerStatus } from "@/lib/types";

export default function TracesPage() {
  const q = useQuery({
    queryKey: ["traces"],
    queryFn: () =>
      get<{ id: string; question: string; status: AnswerStatus; as_of: string; latency_ms: number; created_at: string }[]>("/v1/traces"),
  });
  if (q.isLoading) return <Spinner />;
  if (q.error) return <ErrorNote error={q.error} />;
  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-xl font-semibold">Traces</h1>
        <p className="text-sm text-slate-500">Every query keeps a trace ID with per-node tokens, cost and latency. Open one to replay the debate.</p>
      </div>
      {q.data?.length ? (
        <Card className="divide-y divide-slate-100 dark:divide-slate-800">
          {q.data.map((t) => (
            <Link key={t.id} href={`/traces/${t.id}`} className="flex flex-wrap items-center gap-3 px-4 py-2.5 hover:bg-slate-50 dark:hover:bg-slate-800/50">
              <StatusBadge status={t.status} />
              <span className="min-w-0 flex-1 truncate text-sm">{t.question}</span>
              <span className="text-xs tabular-nums text-slate-500">
                as of {t.as_of} · {Math.round(t.latency_ms)} ms · {t.id}
              </span>
            </Link>
          ))}
        </Card>
      ) : (
        <Empty>No traces yet. Ask a question first.</Empty>
      )}
    </div>
  );
}
