"use client";

import clsx from "clsx";
import { X } from "lucide-react";
import { useEffect, useRef } from "react";
import { STATUS_META, TONE } from "@/lib/format";
import type { AnswerStatus } from "@/lib/types";

export function Card({
  className,
  children,
  ...rest
}: React.HTMLAttributes<HTMLDivElement> & { children: React.ReactNode }) {
  return (
    <div
      className={clsx(
        "rounded-xl border border-slate-200 bg-white shadow-sm dark:border-slate-800 dark:bg-slate-900",
        className,
      )}
      {...rest}
    >
      {children}
    </div>
  );
}

export function SectionTitle({ children, right }: { children: React.ReactNode; right?: React.ReactNode }) {
  return (
    <div className="mb-2 flex items-center justify-between gap-2">
      <h2 className="text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">{children}</h2>
      {right}
    </div>
  );
}

export function Badge({
  tone = "slate",
  children,
  className,
  title,
}: {
  tone?: string;
  children: React.ReactNode;
  className?: string;
  title?: string;
}) {
  const t = TONE[tone] ?? TONE.slate;
  return (
    <span
      title={title}
      className={clsx(
        "inline-flex items-center gap-1 whitespace-nowrap rounded-full border px-2 py-0.5 text-[11px] font-medium",
        t.border,
        t.text,
        t.soft,
        className,
      )}
    >
      {children}
    </span>
  );
}

export function StatusBadge({ status, large }: { status: AnswerStatus; large?: boolean }) {
  const meta = STATUS_META[status];
  const t = TONE[meta.tone];
  return (
    <span
      data-testid="status-badge"
      className={clsx(
        "inline-flex items-center gap-1.5 rounded-full font-semibold text-white",
        t.bg,
        large ? "px-3 py-1 text-sm" : "px-2 py-0.5 text-xs",
      )}
    >
      {meta.label}
    </span>
  );
}

export function Button({
  variant = "primary",
  className,
  children,
  ...rest
}: React.ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "primary" | "ghost" | "outline" | "danger" }) {
  return (
    <button
      className={clsx(
        "inline-flex items-center justify-center gap-1.5 rounded-lg px-3 py-1.5 text-sm font-medium transition disabled:cursor-not-allowed disabled:opacity-50",
        variant === "primary" && "bg-blue-600 text-white hover:bg-blue-700",
        variant === "danger" && "bg-rose-600 text-white hover:bg-rose-700",
        variant === "outline" &&
          "border border-slate-300 bg-white hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-900 dark:hover:bg-slate-800",
        variant === "ghost" && "hover:bg-slate-100 dark:hover:bg-slate-800",
        className,
      )}
      {...rest}
    >
      {children}
    </button>
  );
}

export function Spinner({ className }: { className?: string }) {
  return (
    <span
      role="status"
      aria-label="loading"
      className={clsx("inline-block h-4 w-4 animate-spin rounded-full border-2 border-current border-r-transparent", className)}
    />
  );
}

export function Drawer({
  open,
  onClose,
  title,
  children,
  wide,
}: {
  open: boolean;
  onClose: () => void;
  title: React.ReactNode;
  children: React.ReactNode;
  wide?: boolean;
}) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const prev = document.activeElement as HTMLElement | null;
    ref.current?.focus();
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("keydown", onKey);
      prev?.focus();
    };
  }, [open, onClose]);
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-40 flex justify-end" role="dialog" aria-modal="true" aria-label={typeof title === "string" ? title : undefined}>
      <button aria-label="Close" className="absolute inset-0 bg-slate-900/40 backdrop-blur-[1px]" onClick={onClose} />
      <div
        ref={ref}
        tabIndex={-1}
        className={clsx(
          "pop-in relative flex h-full w-full flex-col overflow-hidden bg-white shadow-2xl outline-none dark:bg-slate-900",
          wide ? "max-w-3xl" : "max-w-xl",
        )}
      >
        <div className="flex items-center justify-between border-b border-slate-200 px-5 py-3 dark:border-slate-800">
          <div className="min-w-0 text-base font-semibold">{title}</div>
          <Button variant="ghost" onClick={onClose} aria-label="Close panel">
            <X className="h-4 w-4" />
          </Button>
        </div>
        <div className="flex-1 overflow-y-auto px-5 py-4">{children}</div>
      </div>
    </div>
  );
}

export function Empty({ children }: { children: React.ReactNode }) {
  return (
    <div className="rounded-lg border border-dashed border-slate-300 p-6 text-center text-sm text-slate-500 dark:border-slate-700">
      {children}
    </div>
  );
}

export function ErrorNote({ error }: { error: unknown }) {
  if (!error) return null;
  return (
    <div role="alert" className="rounded-lg border border-rose-300 bg-rose-50 px-3 py-2 text-sm text-rose-800 dark:border-rose-800 dark:bg-rose-950/40 dark:text-rose-200">
      {error instanceof Error ? error.message : String(error)}
    </div>
  );
}
