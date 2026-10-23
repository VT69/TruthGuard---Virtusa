"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowDown, ArrowUp, FlaskConical, Save, Undo2 } from "lucide-react";
import { useEffect, useState } from "react";
import { Badge, Button, Card, ErrorNote, SectionTitle, Spinner, StatusBadge } from "@/components/ui";
import { get, reResolve, send } from "@/lib/api";
import { humanize, RULE_NAMES } from "@/lib/format";
import type { FinalAnswer, Policy, RuleId } from "@/lib/types";

const RULE_HELP: Record<string, string> = {
  R0: "Excludes sources not in force on the as-of date.",
  R1: "A lineage edge (supersedes / amends) decides for the clause it covers.",
  R2: "Higher authority tier wins; a newer lower-tier source escalates instead.",
  R3: "A rule for a specific scope beats the general rule for that scope.",
  R4: "Within a series, the newer version wins (inferred supersession).",
  R5: "Nothing decided: the answer is UNRESOLVED_CONFLICT.",
};

export default function PolicyPage() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["policy"], queryFn: () => get<{ policy: Policy; default: Policy }>("/v1/policy/precedence") });
  const traces = useQuery({
    queryKey: ["traces"],
    queryFn: () => get<{ id: string; question: string; status: string }[]>("/v1/traces"),
  });
  const [draft, setDraft] = useState<Policy | null>(null);
  const [traceId, setTraceId] = useState<string>("");
  const [preview, setPreview] = useState<{ before: FinalAnswer; after: FinalAnswer } | null>(null);
  useEffect(() => {
    if (q.data && !draft) setDraft(q.data.policy);
  }, [q.data, draft]);
  useEffect(() => {
    if (!traceId && traces.data?.length) setTraceId(traces.data[0].id);
  }, [traces.data, traceId]);

  const save = useMutation({
    mutationFn: (p: Policy) => send<{ policy: Policy }>("/v1/policy/precedence", p, "PUT"),
    onSuccess: (r) => {
      setDraft(r.policy);
      qc.invalidateQueries();
    },
  });
  const tryIt = useMutation({
    mutationFn: async () => {
      const [before, after] = await Promise.all([
        reResolve(traceId, {}),
        reResolve(traceId, { policy: draft! }),
      ]);
      return { before, after };
    },
    onSuccess: setPreview,
  });

  if (q.isLoading || !draft) return <Spinner />;
  if (q.error) return <ErrorNote error={q.error} />;

  const move = (i: number, d: -1 | 1) => {
    const order = [...draft.order];
    const j = i + d;
    if (j < 0 || j >= order.length || order[j] === "R5" || order[i] === "R5") return;
    [order[i], order[j]] = [order[j], order[i]];
    setDraft({ ...draft, order });
  };
  const recencyFirst = () =>
    setDraft({
      ...draft,
      order: ["R0", "R1", "R4", "R2", "R3", "R5"],
      rules: { ...draft.rules, R4: { cross_series: true } },
    });

  const winner = (a: FinalAnswer) =>
    a.claims.filter((c) => c.verdict === "winner" || c.verdict === "position").map((c) => c.value_display).join(" / ") || "—";

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-xl font-semibold">Precedence policy</h1>
        <p className="text-sm text-slate-500">
          Precedence is policy, so it is configured, versioned and deterministic. No LLM decides which source wins.
        </p>
      </div>
      <div className="grid gap-4 lg:grid-cols-2">
        <Card className="p-4">
          <SectionTitle
            right={
              <Button variant="outline" onClick={recencyFirst} className="text-xs" data-testid="preset-recency">
                Preset: recency beats authority
              </Button>
            }
          >
            Rule order
          </SectionTitle>
          <ol className="space-y-1.5" data-testid="rule-order">
            {draft.order.map((r, i) => (
              <li key={r} className="flex items-center gap-2 rounded-lg border border-slate-200 px-3 py-2 dark:border-slate-800">
                <span className="w-8 font-mono text-xs font-bold">{r}</span>
                <span className="flex-1 text-sm">
                  <b>{RULE_NAMES[r as RuleId]}</b>
                  <span className="block text-xs text-slate-500">{RULE_HELP[r]}</span>
                </span>
                <Button variant="ghost" aria-label={`Move ${r} up`} onClick={() => move(i, -1)} disabled={i === 0 || r === "R5"}>
                  <ArrowUp className="h-4 w-4" />
                </Button>
                <Button
                  variant="ghost"
                  aria-label={`Move ${r} down`}
                  onClick={() => move(i, 1)}
                  disabled={r === "R5" || draft.order[i + 1] === "R5"}
                >
                  <ArrowDown className="h-4 w-4" />
                </Button>
              </li>
            ))}
          </ol>
          <div className="mt-3 space-y-2 text-sm">
            <label className="flex items-center gap-2">
              <input
                type="checkbox"
                className="h-4 w-4 accent-blue-600"
                checked={draft.rules.R2.escalate_if_lower_tier_newer}
                onChange={(e) => setDraft({ ...draft, rules: { ...draft.rules, R2: { escalate_if_lower_tier_newer: e.target.checked } } })}
              />
              R2: escalate when a lower-tier source is newer (possible unofficial update)
            </label>
            <label className="flex items-center gap-2">
              <input
                type="checkbox"
                className="h-4 w-4 accent-blue-600"
                checked={draft.rules.R4.cross_series}
                onChange={(e) => setDraft({ ...draft, rules: { ...draft.rules, R4: { cross_series: e.target.checked } } })}
                data-testid="cross-series"
              />
              R4: recency decides across series and tiers
            </label>
          </div>
        </Card>
        <Card className="p-4">
          <SectionTitle>Authority tiers (1 = most authoritative)</SectionTitle>
          <div className="grid grid-cols-2 gap-2">
            {Object.entries(draft.tiers).map(([t, v]) => (
              <label key={t} className="flex items-center justify-between gap-2 text-sm">
                <span>{humanize(t)}</span>
                <input
                  type="number"
                  min={1}
                  max={9}
                  value={v}
                  onChange={(e) => setDraft({ ...draft, tiers: { ...draft.tiers, [t]: Number(e.target.value) || 1 } })}
                  className="w-16 rounded-md border border-slate-300 px-2 py-1 text-right dark:border-slate-700 dark:bg-slate-950"
                />
              </label>
            ))}
          </div>
        </Card>
      </div>

      <div className="flex flex-wrap gap-2">
        <Button onClick={() => save.mutate(draft)} disabled={save.isPending} data-testid="save-policy">
          {save.isPending ? <Spinner /> : <Save className="h-4 w-4" />} Save policy
        </Button>
        <Button variant="outline" onClick={() => q.data && save.mutate(q.data.default)}>
          <Undo2 className="h-4 w-4" /> Restore default
        </Button>
        {save.isSuccess && <Badge tone="emerald">Saved. The corpus audit re-ran with the new policy.</Badge>}
      </div>
      <ErrorNote error={save.error} />

      <Card className="p-4">
        <SectionTitle>Try it on a past question</SectionTitle>
        <div className="flex flex-wrap items-center gap-2">
          <select
            value={traceId}
            onChange={(e) => setTraceId(e.target.value)}
            className="min-w-0 max-w-full flex-1 rounded-lg border border-slate-300 bg-white px-2 py-1.5 text-sm dark:border-slate-700 dark:bg-slate-950"
            aria-label="Past question"
          >
            {traces.data?.map((t) => (
              <option key={t.id} value={t.id}>
                {t.question}
              </option>
            ))}
          </select>
          <Button variant="outline" onClick={() => tryIt.mutate()} disabled={!traceId || tryIt.isPending} data-testid="try-policy">
            {tryIt.isPending ? <Spinner /> : <FlaskConical className="h-4 w-4" />} Compare saved vs draft policy
          </Button>
        </div>
        <ErrorNote error={tryIt.error} />
        {preview && (
          <div className="mt-3 grid gap-3 md:grid-cols-2" data-testid="policy-preview">
            {(["before", "after"] as const).map((k) => (
              <div key={k} className="rounded-lg border border-slate-200 p-3 dark:border-slate-800">
                <div className="mb-1 text-xs font-semibold uppercase text-slate-500">{k === "before" ? "Saved policy" : "Draft policy"}</div>
                <div className="flex items-center gap-2">
                  <StatusBadge status={preview[k].status} />
                  <span className="text-lg font-semibold">{winner(preview[k])}</span>
                </div>
                {preview[k].conflicts[0] && (
                  <div className="mt-1 text-xs text-slate-500">
                    decided by {preview[k].conflicts[0].resolution.deciding_rule ?? "—"}: {preview[k].conflicts[0].resolution.rationale}
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </Card>
    </div>
  );
}
