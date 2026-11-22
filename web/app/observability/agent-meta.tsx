import type { ReactNode } from "react";
import type { AgentLogEntry } from "@/lib/api";

export type AgentId =
  | "overview"
  | "watcher"
  | "ingest"
  | "reconciler"
  | "chaser"
  | "planner"
  | "risk"
  | "human";

export interface AgentMeta {
  id: AgentId;
  name: string;
  shortName: string;
  role: string;
  /** One short line for roster cards. */
  job: string;
  /** Name on roster cards (its initials become the avatar). */
  rosterName: string;
  color: string;
  icon: ReactNode;
}

const svgProps = {
  width: 20,
  height: 20,
  viewBox: "0 0 24 24",
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 2,
  strokeLinecap: "round" as const,
  strokeLinejoin: "round" as const,
  "aria-hidden": true,
};

export const AGENT_NAV: AgentMeta[] = [
  {
    id: "overview",
    name: "All agents",
    shortName: "Overview",
    role: "What every agent did, and what people decided",
    job: "Every agent at a glance",
    rosterName: "All agents",
    color: "#16213a",
    icon: (
      <svg {...svgProps}>
        <rect x="3" y="3" width="7" height="7" rx="1.5" />
        <rect x="14" y="3" width="7" height="7" rx="1.5" />
        <rect x="3" y="14" width="7" height="7" rx="1.5" />
        <rect x="14" y="14" width="7" height="7" rx="1.5" />
      </svg>
    ),
  },
  {
    id: "watcher",
    name: "Watcher",
    shortName: "Watcher",
    role: "Spots new, changed and removed files on the supplier portal",
    job: "Watches the supplier portal for new files",
    rosterName: "Watcher",
    color: "#5a6478",
    icon: (
      <svg {...svgProps}>
        <circle cx="12" cy="12" r="9" />
        <circle cx="12" cy="12" r="4" />
        <line x1="12" y1="12" x2="18.5" y2="5.5" />
      </svg>
    ),
  },
  {
    id: "ingest",
    name: "Ingest & Normalizer",
    shortName: "Ingest",
    role: "Reads CSVs, spreadsheets, PDFs, scans and replies into one source of truth",
    job: "Reads every file, scan and reply into one truth",
    rosterName: "Ingest",
    color: "#1d5fb4",
    icon: (
      <svg {...svgProps}>
        <ellipse cx="12" cy="5" rx="8" ry="3" />
        <path d="M4 5v6c0 1.66 3.58 3 8 3s8-1.34 8-3V5" />
        <path d="M4 11v6c0 1.66 3.58 3 8 3s8-1.34 8-3v-6" />
      </svg>
    ),
  },
  {
    id: "reconciler",
    name: "Reconciler & Gate",
    shortName: "Reconciler",
    role: "Checks the sources against each other: wrong revisions, mismatches, fraud",
    job: "Checks the sources against each other",
    rosterName: "Reconciler",
    color: "#3b6f8f",
    icon: (
      <svg {...svgProps}>
        <path d="M12 3l7 3v6c0 5-3.5 8-7 9-3.5-1-7-4-7-9V6l7-3z" />
        <path d="M9 12l2 2 4-4" />
      </svg>
    ),
  },
  {
    id: "chaser",
    name: "Chaser",
    shortName: "Chaser",
    role: "Emails suppliers in their language and reads the replies",
    job: "Asks suppliers for what is missing",
    rosterName: "Chaser",
    color: "#2f7a4d",
    icon: (
      <svg {...svgProps}>
        <path d="M22 2L11 13" />
        <path d="M22 2l-7 20-4-9-9-4 20-7z" />
      </svg>
    ),
  },
  {
    id: "planner",
    name: "Launch Planner",
    shortName: "Planner",
    role: "Forecasts packing hour by hour and proposes decisions in € and copies",
    job: "Forecasts packing and proposes decisions",
    rosterName: "Launch Planner",
    color: "#127a7a",
    icon: (
      <svg {...svgProps}>
        <circle cx="12" cy="12" r="9" />
        <polygon points="15.5 8.5 13.5 13.5 8.5 15.5 10.5 10.5 15.5 8.5" />
      </svg>
    ),
  },
  {
    id: "risk",
    name: "Risk Rater",
    shortName: "Risk",
    role: "Rates suppliers, parts and editions AAA–CCC from our data and 5 outside feeds",
    job: "Rates suppliers, parts and editions",
    rosterName: "Risk Rater",
    color: "#a86a12",
    icon: (
      <svg {...svgProps}>
        <path d="M12 14l4-4" />
        <path d="M3.34 17a10 10 0 1 1 17.32 0" />
        <circle cx="12" cy="14" r="1.5" />
      </svg>
    ),
  },
  {
    id: "human",
    name: "Human Audit",
    shortName: "Human",
    role: "Who approved, rejected or overrode what",
    job: "Approves or rejects the decision cards",
    rosterName: "Human gate",
    color: "#16213a",
    icon: (
      <svg {...svgProps}>
        <circle cx="9" cy="8" r="4" />
        <path d="M3 20c0-3.3 2.7-6 6-6s6 2.7 6 6" />
        <path d="M16 11l2 2 4-4" />
      </svg>
    ),
  },
];

export const agentMeta = (id: string): AgentMeta =>
  AGENT_NAV.find((a) => a.id === id) ?? AGENT_NAV[0];

export function isPlumbingEvent(e: AgentLogEntry): boolean {
  return e.action.startsWith("handle:") || e.action === "unhandled";
}

/** What an AI call was for, in plain words. The trace only logs the task id ("[mock] ingest.doc.reply"). */
const AI_TASK: [task: string, text: string][] = [
  ["ingest.doc.reply", "Read a supplier reply"],
  ["ingest.doc", "Read a supplier document"],
  ["ingest.scan_ocr", "Read a scanned document"],
  ["ingest.map_columns", "Matched the columns of a new file"],
  ["planner.wording", "Wrote the decision card text"],
  ["chaser", "Drafted a supplier email"],
];

/** A few words for what the agent did (agent lists): AI calls say what they were for, not "AI reasoning". */
export function actionLine(e: Pick<AgentLogEntry, "action" | "detail" | "data">): string {
  return e.action === "llm_call" ? entryDetail(e) : humanizeAction(e.action).label;
}

/** One plain line for a trace entry: the agent's own detail, except AI calls, which only log a task id. */
export function entryDetail(e: Pick<AgentLogEntry, "action" | "detail" | "data">): string {
  if (e.action !== "llm_call") return e.detail ?? "";
  const task = String(e.data?.task ?? e.detail ?? "").replace(/^\[mock\]\s*/, "");
  const hit = AI_TASK.find(([t]) => task === t || task.startsWith(`${t}.`));
  return hit?.[1] ?? "Used AI for one step";
}

export type ActionTone = "ok" | "warn" | "stop" | "act" | "muted";

const ACTION_MAP: Record<string, { label: string; tone: ActionTone }> = {
  error: { label: "Error", tone: "stop" },
  drop: { label: "Drop received", tone: "act" },
  replay_manifest: { label: "Drop loaded", tone: "act" },
  drop_ingested: { label: "Drop ingested", tone: "ok" },
  table_ingested: { label: "Table loaded", tone: "act" },
  update_row: { label: "Field updated", tone: "act" },
  scan_read: { label: "Scan OCR", tone: "act" },
  alias_learned: { label: "ID matched", tone: "ok" },
  raise_issue: { label: "Finding raised", tone: "warn" },
  update_issue: { label: "Finding updated", tone: "warn" },
  resolve_issue: { label: "Finding resolved", tone: "ok" },
  reconciled: { label: "Checks complete", tone: "ok" },
  risk_reprioritized: { label: "Risk escalated", tone: "warn" },
  risk_rated: { label: "Ratings computed", tone: "act" },
  grade_changed: { label: "Grade changed", tone: "warn" },
  forecast_updated: { label: "Forecast updated", tone: "act" },
  forecast: { label: "Forecast updated", tone: "act" },
  decision_created: { label: "Card proposed", tone: "act" },
  decision_approved: { label: "Executing option", tone: "ok" },
  email_sent: { label: "Email sent", tone: "ok" },
  email_drafted: { label: "Email drafted", tone: "act" },
  mock_send: { label: "Email sent (mock)", tone: "ok" },
  send_email: { label: "Email sent", tone: "ok" },
  draft_email: { label: "Email drafted", tone: "act" },
  agent_start: { label: "Working on a question", tone: "act" },
  agent_done: { label: "Question handled", tone: "ok" },
  unmatched_mail: { label: "Reply not matched", tone: "warn" },
  skip_rate_limit: { label: "Waiting for a reply", tone: "muted" },
  // Logged by the Chaser (nobody to ask) and the Watcher (live check off): the detail line says which.
  skip: { label: "Skipped", tone: "muted" },
  handoff: { label: "Sent to the Chaser", tone: "act" },
  decision_rejected: { label: "Card closed", tone: "muted" },
  awaiting_inputs: { label: "Waiting for data", tone: "muted" },
  delivery_review: { label: "Reply needs review", tone: "warn" },
  confirmed_delivery: { label: "Delivery confirmed", tone: "ok" },
  supplier_quote: { label: "Quote received", tone: "act" },
  check: { label: "Portal checked", tone: "act" },
  no_change: { label: "Nothing new", tone: "muted" },
  file_removed: { label: "File removed", tone: "warn" },
  missing_file: { label: "File missing", tone: "warn" },
  unsupported: { label: "File not read", tone: "warn" },
  changeset_skip: { label: "Change skipped", tone: "muted" },
  read_evidence: { label: "Read the evidence", tone: "act" },
  sent_copy_pending: { label: "Sent copy not saved", tone: "warn" },
  reply_received: { label: "Reply received", tone: "ok" },
  approve: { label: "Human approved", tone: "ok" },
  reject: { label: "Human rejected", tone: "stop" },
  llm_call: { label: "AI reasoning", tone: "muted" },
};

export function humanizeAction(action: string): { label: string; tone: ActionTone } {
  const hit = ACTION_MAP[action];
  if (hit) return hit;
  if (action.startsWith("handle:")) {
    return { label: `Handled ${action.slice(7)}`, tone: "muted" };
  }
  return { label: action.replaceAll("_", " "), tone: "muted" };
}

export const TONE_CLASS: Record<ActionTone, string> = {
  ok: "bg-ok-soft text-ok",
  warn: "bg-warn-soft text-warn",
  stop: "bg-stop-soft text-stop",
  act: "bg-act-soft text-act",
  muted: "bg-dock text-muted",
};
