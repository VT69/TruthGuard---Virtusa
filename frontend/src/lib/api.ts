import type { BaselineAnswer, FinalAnswer, Policy, TraceEvent } from "./types";

export const API_URL = (process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000").replace(/\/$/, "");
const API_KEY = process.env.NEXT_PUBLIC_API_KEY || "";

function headers(extra: Record<string, string> = {}): Record<string, string> {
  return { ...(API_KEY ? { "X-API-Key": API_KEY } : {}), ...extra };
}

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

async function handle<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {
      /* not json */
    }
    throw new ApiError(res.status, detail);
  }
  return (await res.json()) as T;
}

export async function get<T>(path: string): Promise<T> {
  return handle<T>(await fetch(`${API_URL}${path}`, { headers: headers(), cache: "no-store" }));
}

export async function send<T>(path: string, body: unknown, method: "POST" | "PUT" = "POST"): Promise<T> {
  return handle<T>(
    await fetch(`${API_URL}${path}`, {
      method,
      headers: headers({ "Content-Type": "application/json" }),
      body: body === undefined ? undefined : JSON.stringify(body),
    }),
  );
}

export interface QueryArgs {
  question: string;
  as_of?: string | null;
  mode?: "truthguard" | "B0" | "B1" | "B2";
}

/** POST /v1/query and parse the SSE stream; resolves with the final payload. */
export async function streamQuery(
  args: QueryArgs,
  onEvent: (ev: TraceEvent) => void,
  signal?: AbortSignal,
): Promise<FinalAnswer | BaselineAnswer | null> {
  const res = await fetch(`${API_URL}/v1/query`, {
    method: "POST",
    headers: headers({ "Content-Type": "application/json", Accept: "text/event-stream" }),
    body: JSON.stringify({ question: args.question, as_of: args.as_of || null, mode: args.mode || "truthguard" }),
    signal,
  });
  if (!res.ok || !res.body) throw new ApiError(res.status, `query failed: ${res.statusText}`);
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let final: FinalAnswer | BaselineAnswer | null = null;
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let idx: number;
    while ((idx = buffer.indexOf("\n\n")) >= 0) {
      const block = buffer.slice(0, idx);
      buffer = buffer.slice(idx + 2);
      const data = block
        .split("\n")
        .filter((l) => l.startsWith("data: "))
        .map((l) => l.slice(6))
        .join("\n");
      if (!data) continue;
      const ev = JSON.parse(data) as TraceEvent;
      onEvent(ev);
      if (ev.event === "answer.final") final = ev.payload as unknown as FinalAnswer | BaselineAnswer;
      if (ev.event === "error") throw new ApiError(500, String(ev.payload.message ?? "query failed"));
    }
  }
  return final;
}

export const reResolve = (traceId: string, body: { as_of?: string; policy?: Policy }) =>
  send<FinalAnswer>(`/v1/traces/${traceId}/re-resolve`, body);
