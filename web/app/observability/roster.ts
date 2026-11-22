// Turns the live agent trace into roster cards (components/ui/agent-roster.tsx).
import type { AgentStatus, RosterAgent } from "@/components/ui/agent-roster";
import { ago, fmt, parseTs, type AgentLogEntry, type ObservabilitySnapshot } from "@/lib/api";
import { AGENT_NAV, entryDetail, humanizeAction, reportHref, isPlumbingEvent, type AgentId } from "./agent-meta";

/** An agent counts as working while its last real action is younger than this. */
const WORKING_MS = 15_000;

interface AgentStats {
  real: number;
  errors: number;
  /** Newest real (non-plumbing) action; errors are never plumbing. */
  last?: AgentLogEntry;
}

function statsByAgent(trace: AgentLogEntry[]): Map<string, AgentStats> {
  const map = new Map<string, AgentStats>();
  // The backend returns the trace newest first.
  for (const e of trace) {
    const cur = map.get(e.agent) ?? { real: 0, errors: 0 };
    if (!isPlumbingEvent(e)) {
      cur.real += 1;
      cur.last ??= e;
    }
    if (e.action === "error") cur.errors += 1;
    map.set(e.agent, cur);
  }
  return map;
}

export const plural = (n: number, one: string, many = `${one}s`) => `${fmt(n)} ${n === 1 ? one : many}`;

export function buildRosterAgents(
  snap: ObservabilitySnapshot,
  now: number,
  online: boolean | null,
): RosterAgent[] {
  const stats = statsByAgent(snap.trace);
  const workers = AGENT_NAV.filter((a) => a.id !== "overview");
  const maxReal = Math.max(1, ...workers.map((w) => stats.get(w.id)?.real ?? 0));
  const registered = new Set(snap.health?.agents ?? []);
  const proposed = snap.decisions.filter((d) => d.status === "proposed").length;
  const awaitingReply = snap.outbox.filter((m) => m.status === "sent").length;

  // In replay mode drops are logged by the coordinator, not the Watcher: count them as the Watcher's work.
  const newestDrop = snap.drops.reduce<(typeof snap.drops)[number] | undefined>(
    (best, d) => (!best || d.id > best.id ? d : best),
    undefined,
  );

  return workers.map((a): RosterAgent => {
    const traced = stats.get(a.id) ?? { real: 0, errors: 0 };
    let st: AgentStats = traced;
    let lastTsRaw = traced.last?.ts;
    let lastText = traced.last ? entryDetail(traced.last) || humanizeAction(traced.last.action).label : "";
    // Same rule as the control room's Agents panel: blocked only while the newest real step is an error.
    let error = traced.last?.action === "error" ? traced.last : undefined;
    if (a.id === "watcher" && newestDrop && (!lastTsRaw || (parseTs(newestDrop.checked_at)?.getTime() ?? 0) > (parseTs(lastTsRaw)?.getTime() ?? 0))) {
      error = undefined; // a newer drop came in after the Watcher's last step
      st = { ...traced, real: traced.real + snap.drops.length };
      lastTsRaw = newestDrop.checked_at;
      lastText = `Drop ${newestDrop.id}: ${plural(newestDrop.added.length, "file")} added, ${fmt(newestDrop.changed.length)} changed, ${fmt(newestDrop.removed.length)} removed.`;
    }
    const lastTs = parseTs(lastTsRaw);
    const recent = !!lastTs && now > 0 && now - lastTs.getTime() < WORKING_MS;

    let status: AgentStatus;
    let task: string;
    if (online === false) {
      status = "offline";
      task = "Backend is not answering.";
    } else if (error) {
      status = "blocked";
      task = `Error: ${entryDetail(error) || "see the activity log"}`;
    } else if (recent) {
      status = "working";
      task = lastText;
    } else if (a.id === "human" && proposed > 0) {
      status = "waiting";
      task = `${plural(proposed, "decision card")} waiting for approval.`;
    } else if (a.id === "chaser" && awaitingReply > 0) {
      status = "waiting";
      task = `Waiting on ${plural(awaitingReply, "supplier reply", "supplier replies")}. ${lastText}`.trim();
    } else if (!st.real && !lastTs && registered.size > 0 && !registered.has(a.id) && a.id !== "human") {
      status = "offline";
      task = "Not registered in this backend.";
    } else {
      status = "idle";
      task = lastText || "Waiting for the next data drop.";
    }

    return {
      id: a.id satisfies AgentId,
      href: reportHref(a.id),
      name: a.rosterName,
      role: a.job,
      status,
      task,
      load: Math.min(100, Math.round(((st.real ?? 0) / maxReal) * 100)),
      lastActive: lastTsRaw ? ago(lastTsRaw, now) : "never",
    };
  });
}
