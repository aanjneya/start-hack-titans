// Thin client for the FastAPI backend. Types come from the shared contract (repo root API_CONTRACT.ts).
import type {
  AgentLogEntry,
  ApiResponse,
  DecisionCard,
  Evidence,
  FieldChange,
  Forecast,
  HealthCheckResponse,
  InboxMessage,
  Issue,
  OutboxMessage,
  RiskScore,
} from "../../API_CONTRACT";

export type {
  AgentLogEntry,
  DecisionCard,
  DecisionOption,
  Evidence,
  FieldChange,
  Forecast,
  HealthCheckResponse,
  InboxMessage,
  Issue,
  OutboxMessage,
  RiskScore,
  RunResult,
} from "../../API_CONTRACT";

export interface DropRecord {
  id: number;
  label: string;
  mode: string;
  checked_at: string;
  added: string[];
  changed: string[];
  removed: string[];
}

export interface Conflict {
  id: number;
  entity: string;
  entity_id: string;
  field: string;
  value_a: string;
  source_a: string;
  value_b: string;
  source_b: string;
  status: string;
  created_at: string;
  resolved_at: string | null;
}

export interface ExtendedRiskReason {
  text: string;
  factor?: string;
  points?: number;
  score_0_100?: number;
  breakdown?: Record<string, number>;
  evidence?: Evidence[];
  recommended_action?: Record<string, unknown>;
}

export interface ObservabilitySnapshot {
  health: HealthCheckResponse | null;
  trace: AgentLogEntry[];
  drops: DropRecord[];
  changes: FieldChange[];
  issues: Issue[];
  conflicts: Conflict[];
  risk: RiskScore[];
  decisions: DecisionCard[];
  forecast: Forecast[];
  outbox: OutboxMessage[];
  inbox: InboxMessage[];
}

export const EMPTY_OBSERVABILITY: ObservabilitySnapshot = {
  health: null,
  trace: [],
  drops: [],
  changes: [],
  issues: [],
  conflicts: [],
  risk: [],
  decisions: [],
  forecast: [],
  outbox: [],
  inbox: [],
};

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

export async function loadObservabilitySnapshot(): Promise<{ snap: ObservabilitySnapshot; online: boolean }> {
  const [health, trace, drops, changes, issues, conflicts, risk, decisions, forecast, outbox, inbox] =
    await Promise.allSettled([
      getData<HealthCheckResponse>("/api/health"),
      getData<AgentLogEntry[]>("/api/trace?limit=500"),
      getData<DropRecord[]>("/api/drops"),
      getData<FieldChange[]>("/api/changes?limit=300"),
      getData<Issue[]>("/api/issues"),
      getData<Conflict[]>("/api/conflicts"),
      getData<RiskScore[]>("/api/risk"),
      getData<DecisionCard[]>("/api/decisions"),
      getData<Forecast[]>("/api/forecast"),
      getData<OutboxMessage[]>("/api/outbox"),
      getData<InboxMessage[]>("/api/inbox"),
    ]);
  const val = <T,>(r: PromiseSettledResult<T>, fallback: T) => (r.status === "fulfilled" ? r.value : fallback);
  return {
    online: health.status === "fulfilled",
    snap: {
      health: val(health, null),
      trace: val(trace, []),
      drops: val(drops, []),
      changes: val(changes, []),
      issues: val(issues, []),
      conflicts: val(conflicts, []),
      risk: val(risk, []),
      decisions: val(decisions, []),
      forecast: val(forecast, []),
      outbox: val(outbox, []),
      inbox: val(inbox, []),
    },
  };
}
