"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { Moon, RotateCcw, ShieldCheck, Sun } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { get, send } from "@/lib/api";
import { fmtDate } from "@/lib/format";
import type { Meta } from "@/lib/types";
import { Badge, Button, Spinner } from "./ui";

const NAV = [
  { href: "/", label: "Ask" },
  { href: "/health", label: "Corpus Health" },
  { href: "/lineage", label: "Lineage" },
  { href: "/policy", label: "Policy" },
  { href: "/traces", label: "Traces" },
  { href: "/eval", label: "Eval" },
];

export function TopBar() {
  const path = usePathname();
  const qc = useQueryClient();
  const meta = useQuery({ queryKey: ["meta"], queryFn: () => get<Meta>("/v1/meta"), retry: 0 });
  const reset = useMutation({
    mutationFn: () => send<{ removed_documents: number }>("/v1/admin/reset", undefined),
    onSuccess: () => qc.invalidateQueries(),
  });
  const [dark, setDark] = useState(false);
  useEffect(() => setDark(document.documentElement.classList.contains("dark")), []);
  const toggle = () => {
    const next = !dark;
    setDark(next);
    document.documentElement.classList.toggle("dark", next);
    try {
      localStorage.setItem("tg-theme", next ? "dark" : "light");
    } catch {
      /* private mode */
    }
  };

  return (
    <header className="sticky top-0 z-30 border-b border-slate-200 bg-white/90 backdrop-blur dark:border-slate-800 dark:bg-slate-950/90">
      <div className="mx-auto flex max-w-[1500px] flex-wrap items-center gap-x-4 gap-y-2 px-4 py-2.5 sm:px-6">
        <Link href="/" className="flex items-center gap-2 font-semibold">
          <ShieldCheck className="h-5 w-5 text-blue-600" aria-hidden />
          TruthGuard
        </Link>
        <nav aria-label="Main" className="flex flex-wrap gap-1 text-sm">
          {NAV.map((n) => {
            const active = n.href === "/" ? path === "/" : path.startsWith(n.href);
            return (
              <Link
                key={n.href}
                href={n.href}
                aria-current={active ? "page" : undefined}
                className={clsx(
                  "rounded-md px-2.5 py-1",
                  active
                    ? "bg-slate-900 text-white dark:bg-slate-100 dark:text-slate-900"
                    : "text-slate-600 hover:bg-slate-100 dark:text-slate-300 dark:hover:bg-slate-800",
                )}
              >
                {n.label}
              </Link>
            );
          })}
        </nav>
        <div className="ml-auto flex items-center gap-2">
          {meta.data ? (
            <>
              <Badge tone={meta.data.llm_mode === "offline" ? "slate" : "emerald"} title="LLM mode">
                LLM: {meta.data.llm_mode}
              </Badge>
              {meta.data.demo_mode && <Badge tone="sky">Demo mode</Badge>}
              <Badge tone="slate" title="Default as-of date">
                today {fmtDate(meta.data.today)}
              </Badge>
              <Badge tone="slate" className="hidden sm:inline-flex">
                {meta.data.corpus.documents} docs · {meta.data.corpus.assertions} facts
              </Badge>
            </>
          ) : meta.isError ? (
            <Badge tone="rose">API offline</Badge>
          ) : null}
          <Button
            variant="outline"
            onClick={() => reset.mutate()}
            disabled={reset.isPending}
            title="Remove judge-added documents and restore the default precedence policy"
            data-testid="reset-button"
          >
            {reset.isPending ? <Spinner /> : <RotateCcw className="h-4 w-4" />} Reset demo
          </Button>
          <Button variant="ghost" onClick={toggle} aria-label={dark ? "Switch to light mode" : "Switch to dark mode"}>
            {dark ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
          </Button>
        </div>
      </div>
    </header>
  );
}
