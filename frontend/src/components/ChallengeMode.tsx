"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Check, Swords } from "lucide-react";
import { useState } from "react";
import { send } from "@/lib/api";
import { Badge, Button, Drawer, ErrorNote, Spinner } from "./ui";

interface QuickResult {
  document_id: string;
  chunks: number;
  assertions: number;
  injection_flag: boolean;
  timings_ms: Record<string, number>;
  authority_tier: number | null;
  assertions_detail: { subject_id: string; attribute: string; value_display: string; scope: string; quarantined: boolean }[];
}

const DOC_TYPES = ["memo", "email", "faq", "help_article", "notice", "circular", "policy", "amendment", "board_policy"];

export function ChallengeMode({
  open,
  onClose,
  onReask,
  today,
}: {
  open: boolean;
  onClose: () => void;
  onReask: () => void;
  today: string;
}) {
  const qc = useQueryClient();
  const [title, setTitle] = useState("Branch memo: Platinum fee update");
  const [docType, setDocType] = useState("memo");
  const [date, setDate] = useState(today);
  const [text, setText] = useState("Effective today, the late payment fee on the Platinum Credit Card is ₹1,200.");
  const add = useMutation({
    mutationFn: () =>
      send<QuickResult>("/v1/documents/quick", { title, text, doc_type: docType, effective_from: date || null }),
    onSuccess: () => qc.invalidateQueries(),
  });

  return (
    <Drawer
      open={open}
      onClose={onClose}
      title={
        <span className="inline-flex items-center gap-2">
          <Swords className="h-4 w-4 text-rose-600" aria-hidden /> Challenge Mode
        </span>
      }
    >
      <p className="mb-3 text-sm text-slate-600 dark:text-slate-300">
        Plant a contradiction in the knowledge base. TruthGuard ingests it in a fraction of a second; re-ask the question and
        watch the analyst, resolver and critic deal with it.
      </p>
      <form
        className="space-y-3"
        onSubmit={(e) => {
          e.preventDefault();
          add.mutate();
        }}
      >
        <label className="block text-sm">
          <span className="font-medium">Title</span>
          <input
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            className="mt-1 w-full rounded-lg border border-slate-300 bg-white px-3 py-2 dark:border-slate-700 dark:bg-slate-950"
          />
        </label>
        <div className="grid grid-cols-2 gap-3">
          <label className="block text-sm">
            <span className="font-medium">Document type</span>
            <select
              value={docType}
              onChange={(e) => setDocType(e.target.value)}
              className="mt-1 w-full rounded-lg border border-slate-300 bg-white px-3 py-2 dark:border-slate-700 dark:bg-slate-950"
            >
              {DOC_TYPES.map((t) => (
                <option key={t} value={t}>
                  {t.replace(/_/g, " ")}
                </option>
              ))}
            </select>
          </label>
          <label className="block text-sm">
            <span className="font-medium">Effective from</span>
            <input
              type="date"
              value={date}
              onChange={(e) => setDate(e.target.value)}
              className="mt-1 w-full rounded-lg border border-slate-300 bg-white px-3 py-2 dark:border-slate-700 dark:bg-slate-950"
            />
          </label>
        </div>
        <label className="block text-sm">
          <span className="font-medium">Text</span>
          <textarea
            value={text}
            onChange={(e) => setText(e.target.value)}
            rows={4}
            data-testid="challenge-text"
            className="mt-1 w-full rounded-lg border border-slate-300 bg-white px-3 py-2 dark:border-slate-700 dark:bg-slate-950"
          />
        </label>
        <Button type="submit" disabled={add.isPending} data-testid="challenge-add">
          {add.isPending ? <Spinner /> : null} Add to knowledge base
        </Button>
      </form>
      <div className="mt-4">
        <ErrorNote error={add.error} />
      </div>
      {add.data && (
        <div className="mt-4 space-y-3" data-testid="challenge-result">
          <ol className="space-y-1">
            {Object.entries(add.data.timings_ms).map(([stage, ms]) => (
              <li key={stage} className="pop-in flex items-center gap-2 text-sm">
                <Check className="h-4 w-4 text-emerald-600" aria-hidden />
                <span className="w-24 capitalize">{stage}</span>
                <span className="tabular-nums text-slate-500">{ms.toFixed(1)} ms</span>
              </li>
            ))}
          </ol>
          <div className="rounded-lg bg-slate-50 p-3 text-sm dark:bg-slate-800/60">
            <div className="mb-1 font-medium">TruthGuard read {add.data.assertions} fact(s):</div>
            {add.data.assertions_detail.map((a, i) => (
              <div key={i} className="text-xs">
                {a.attribute.replace(/_/g, " ")} · {a.subject_id.replace(/_/g, " ")} = <b>{a.value_display}</b>
                {a.quarantined && <Badge tone="rose" className="ml-1">quarantined</Badge>}
              </div>
            ))}
            <div className="mt-1 text-xs text-slate-500">Authority tier {add.data.authority_tier}</div>
          </div>
          <Button
            onClick={() => {
              onClose();
              onReask();
            }}
            data-testid="challenge-reask"
          >
            Re-ask the question
          </Button>
        </div>
      )}
    </Drawer>
  );
}
