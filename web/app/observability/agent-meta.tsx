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
    name: "All Agents Overview",
    shortName: "Overview",
    role: "Live accountability across every AI agent and human gate",
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
    role: "Detects new, modified, or removed supplier portal files",
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
    role: "Normalizes CSVs, Excel, multilingual PDFs, scans, and replies into one truth",
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
    role: "Cross-checks sources for traps, ECN revision blocks, and fraud anomalies",
    color: "#6b4fbb",
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
    role: "Sends rate-limited multilingual supplier emails and captures replies",
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
    role: "Simulates hour-by-hour packing and builds € vs copies decision cards",
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
    role: "Moody's AAA–CCC ratings fusing internal health + 5 live external & regulatory feeds",
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
    role: "Accountability trail of human approvals, rejections, and overrides",
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

export type ActionTone = "ok" | "warn" | "stop" | "act" | "muted";

const ACTION_MAP: Record<string, { label: string; tone: ActionTone }> = {
  error: { label: "Error", tone: "stop" },
  drop: { label: "Drop received", tone: "act" },
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
  decision_created: { label: "Card proposed", tone: "act" },
  decision_approved: { label: "Executing option", tone: "ok" },
  email_sent: { label: "Email sent", tone: "ok" },
  email_drafted: { label: "Email drafted", tone: "act" },
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
