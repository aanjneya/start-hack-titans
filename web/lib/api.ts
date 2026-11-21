// Thin client for the FastAPI backend. Types come from the shared contract (repo root API_CONTRACT.ts).
import type { ApiResponse } from "../../API_CONTRACT";

export type {
  AgentLogEntry,
  DecisionCard,
  DecisionOption,
  Evidence,
  Forecast,
  HealthCheckResponse,
  InboxMessage,
  Issue,
  OutboxMessage,
  RiskScore,
  RunResult,
} from "../../API_CONTRACT";

export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

async function unwrap<T>(res: Response, path: string): Promise<T> {
  const body = (await res.json().catch(() => null)) as (ApiResponse<T> & { detail?: unknown }) | null;
  if (!res.ok || !body?.ok) {
    // FastAPI errors come as {detail: "..."}; our own as the ApiResponse envelope.
    const reason = body?.error?.message ?? (typeof body?.detail === "string" ? body.detail : null);
    throw new Error(reason ?? `${res.status} ${res.statusText} on ${path}`);
  }
  return body.data as T;
}

export async function getData<T>(path: string): Promise<T> {
  return unwrap<T>(await fetch(`${API_URL}${path}`, { cache: "no-store" }), path);
}

export async function postData<T>(path: string, payload?: unknown): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: payload === undefined ? undefined : JSON.stringify(payload),
  });
  return unwrap<T>(res, path);
}

/** Backend timestamps are UTC; some come without a zone suffix. */
export function parseTs(ts: string | null | undefined): Date | null {
  if (!ts) return null;
  return new Date(/[zZ]|[+-]\d\d:\d\d$/.test(ts) ? ts : `${ts.replace(" ", "T")}Z`);
}

const num = new Intl.NumberFormat("en-US");
export const fmt = (n: number | null | undefined) => (n == null ? "–" : num.format(Math.round(n)));
export const eur = (n: number | null | undefined) => (n == null ? "–" : `€${num.format(Math.round(n))}`);

export function clock(ts: string | null | undefined): string {
  const d = parseTs(ts);
  return d
    ? d.toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit", second: "2-digit", timeZone: "Europe/Berlin" })
    : "";
}

export function ago(ts: string | null | undefined, now: number): string {
  const d = parseTs(ts);
  if (!d) return "never";
  const s = Math.max(0, Math.round((now - d.getTime()) / 1000));
  if (s < 60) return `${s} s ago`;
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  return `${Math.round(s / 3600)} h ago`;
}
