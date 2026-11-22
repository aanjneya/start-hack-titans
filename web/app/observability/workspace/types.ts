// Contract between the workspace chat (chat-panel.tsx) and the agent "brain" (brain.ts).
import type { ObservabilitySnapshot } from "@/lib/api";
import type { TruthData } from "./data";

export type WorkspaceAgent = "watcher" | "ingest";

/** Colour of a value inside an answer table. */
export type CellTone = "stop" | "warn" | "ok" | "muted" | "strong" | "ai";

export type AnswerCell = string | number | { value: string | number; tone?: CellTone };

export type AnswerBlock =
  /** One paragraph: `lead` in bold, then `text` in the same paragraph. */
  | { kind: "text"; lead?: string; text?: string }
  /** A small table, like "Promo week | Need | Have | Short". Numbers right-aligned via `align`. */
  | { kind: "table"; columns: { label: string; align?: "left" | "right" }[]; rows: AnswerCell[][] }
  | { kind: "bullets"; items: string[] };

export interface AgentAnswer {
  /** Header of the finished step card, e.g. "Checked stock, open POs and the forecast". */
  title: string;
  blocks: AnswerBlock[];
  /** Row ids in the left table this answer is about: part ids (ingest) or file paths (watcher). */
  highlight: string[];
  /** Two or three short follow-up questions, shown as chips under the answer. */
  followUps: string[];
}

export interface RunStep {
  id: string;
  /** What the agent does, e.g. "Read the stock snapshot". */
  title: string;
  /** Where it looked, shown as small chips after the title, e.g. ["truth store"], ["Planner"]. */
  tags?: string[];
  /**
   * Does the real work (fetches from the backend and/or computes). Resolves to the muted detail
   * line shown under the title once done, e.g. "35 lots · stock_snapshot.csv". Rejects on failure.
   */
  run: () => Promise<string | undefined>;
}

export interface PlannedRun {
  /** Short machine name of the matched question type, e.g. "launch_cover". */
  intent: string;
  /** Header while the steps run, e.g. "Checking stock against the launch". */
  workingTitle: string;
  steps: RunStep[];
  /** Builds the answer from what the steps loaded. Called once, after every step resolved. */
  compose: () => AgentAnswer;
}

export interface BrainContext {
  agent: WorkspaceAgent;
  /** Latest observability snapshot (trace, drops, changes, issues, forecast, decisions, outbox, …). */
  snap: ObservabilitySnapshot;
  /** Truth store as last loaded for the table (may be null before the first load). */
  truth: TruthData | null;
}
