"use client";

import { useMemo, useState } from "react";
import {
  ago, clock, eur, fmt, parseTs,
  type AgentLogEntry, type Conflict, type Evidence, type ExtendedRiskReason,
  type Forecast, type ObservabilitySnapshot, type RiskScore,
} from "@/lib/api";
import {
  AGENT_NAV, TONE_CLASS, agentMeta, humanizeAction, isPlumbingEvent, type AgentId,
} from "./agent-meta";
import RiskPlaybook from "../risk-playbook";

// ---------------------------------------------------------------------------
// Shared visual primitives
// ---------------------------------------------------------------------------
type KpiItem = {
  label: string;
  value: string | number;
  hint?: string;
  tone?: "default" | "ok" | "warn" | "stop" | "act";
};

const KPI_COLOR: Record<NonNullable<KpiItem["tone"]>, string> = {
  default: "text-ink",
  ok: "text-ok",
  warn: "text-warn",
  stop: "text-stop",
  act: "text-act",
};

function KpiGrid({ items }: { items: KpiItem[] }) {
  return (
    <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
      {items.map((item) => (
        <div key={item.label} className="rounded-xl border border-rule bg-surface px-4 py-4">
          <p className="text-xs font-semibold uppercase tracking-wide text-muted">{item.label}</p>
          <p className={`figure mt-2 text-4xl ${KPI_COLOR[item.tone ?? "default"]}`}>{item.value}</p>
          {item.hint && <p className="mt-1.5 text-xs text-muted">{item.hint}</p>}
        </div>
      ))}
    </div>
  );
}

function StackedBar({
  title,
  segments,
}: {
  title: string;
  segments: { label: string; count: number; color: string }[];
}) {
  const total = segments.reduce((acc, s) => acc + s.count, 0);
  return (
    <div className="rounded-xl border border-rule bg-surface p-4">
      <div className="flex items-baseline justify-between gap-2">
        <h3 className="text-sm font-bold">{title}</h3>
        <span className="text-xs text-muted tabular">{fmt(total)} total</span>
      </div>
      {total === 0 ? (
        <p className="mt-3 text-sm text-muted">No activity recorded yet.</p>
      ) : (
        <>
          <div className="mt-3 flex h-3 w-full overflow-hidden rounded-full bg-dock" role="img" aria-label={title}>
            {segments
              .filter((s) => s.count > 0)
              .map((s) => (
                <div
                  key={s.label}
                  title={`${s.label}: ${s.count}`}
                  style={{ width: `${(s.count / total) * 100}%`, background: s.color }}
                />
              ))}
          </div>
          <ul className="mt-3 flex flex-wrap gap-x-4 gap-y-1.5 text-xs">
            {segments.map((s) => (
              <li key={s.label} className="flex items-center gap-1.5">
                <span className="inline-block h-2.5 w-2.5 rounded-sm" style={{ background: s.color }} aria-hidden />
                <span className="text-muted">{s.label}:</span>
                <span className="font-semibold text-ink tabular">{fmt(s.count)}</span>
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  );
}

function EvidenceList({ evidence }: { evidence: Evidence[] }) {
  if (!evidence.length) return null;
  return (
    <ul className="mt-2 space-y-1 text-xs">
      {evidence.slice(0, 6).map((e, idx) => {
        const isUrl = /^https?:\/\//i.test(e.path);
        return (
          <li key={idx} className="break-words rounded bg-dock/70 px-2.5 py-1.5 text-muted">
            {isUrl ? (
              <a
                href={e.path}
                target="_blank"
                rel="noreferrer"
                className="font-semibold text-act underline hover:text-ink"
              >
                {e.path}
              </a>
            ) : (
              <span className="font-semibold text-ink">{e.path || "internal"}</span>
            )}
            {e.locator && <span> · {e.locator}</span>}
            {e.quote && <span className="block italic text-muted">“{e.quote}”</span>}
          </li>
        );
      })}
    </ul>
  );
}

function EmptyState({ text }: { text: string }) {
  return (
    <div className="rounded-xl border border-dashed border-rule bg-surface px-6 py-8 text-center text-sm text-muted">
      {text}
    </div>
  );
}

// ---------------------------------------------------------------------------
// 1. Overview Report (All Agents)
// ---------------------------------------------------------------------------
export function OverviewReport({
  snap,
  now,
  onSelectAgent,
}: {
  snap: ObservabilitySnapshot;
  now: number;
  onSelectAgent: (id: AgentId) => void;
}) {
  const realEvents = useMemo(() => snap.trace.filter((e) => !isPlumbingEvent(e)), [snap.trace]);
  const totalErrors = useMemo(() => snap.trace.filter((e) => e.action === "error").length, [snap.trace]);
  const openIssues = useMemo(() => snap.issues.filter((i) => i.status !== "resolved").length, [snap.issues]);
  const humanOverrides = useMemo(
    () => snap.decisions.filter((d) => d.status === "rejected").length,
    [snap.decisions],
  );

  const agentStats = useMemo(() => {
    const map = new Map<string, { total: number; real: number; errors: number; last?: AgentLogEntry }>();
    for (const e of snap.trace) {
      const cur = map.get(e.agent) ?? { total: 0, real: 0, errors: 0 };
      cur.total += 1;
      if (!isPlumbingEvent(e)) {
        cur.real += 1;
        if (!cur.last) cur.last = e;
      }
      if (e.action === "error") cur.errors += 1;
      map.set(e.agent, cur);
    }
    return map;
  }, [snap.trace]);

  const workers = AGENT_NAV.filter((a) => a.id !== "overview");
  const maxReal = Math.max(1, ...workers.map((w) => agentStats.get(w.id)?.real ?? 0));

  return (
    <div className="space-y-6">
      <KpiGrid
        items={[
          { label: "Total Agent Actions", value: fmt(realEvents.length), hint: `${fmt(snap.drops.length)} data drops processed`, tone: "act" },
          { label: "Open Findings", value: fmt(openIssues), hint: `${fmt(snap.conflicts.filter((c) => c.status === "open").length)} open source conflicts`, tone: openIssues > 0 ? "warn" : "ok" },
          { label: "Decisions Executed / Approved", value: fmt(snap.decisions.filter((d) => d.status === "approved" || d.status === "executed").length), hint: `${fmt(snap.decisions.filter((d) => d.status === "proposed").length)} waiting on human`, tone: "ok" },
          {
            label: "Errors & Human Overrides",
            value: fmt(totalErrors + humanOverrides),
            hint: `${fmt(totalErrors)} errors · ${fmt(humanOverrides)} human rejections`,
            tone: totalErrors > 0 ? "stop" : humanOverrides > 0 ? "warn" : "ok",
          },
        ]}
      />

      <section aria-label="Agent cards">
        <div className="mb-3 flex items-baseline justify-between">
          <h2 className="text-lg font-bold">Parallel Agent Fleet — Click any card to inspect</h2>
          <span className="text-xs text-muted">Office hours traceability: per-agent actions, outputs & errors</span>
        </div>
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
          {workers.map((agent) => {
            const st = agentStats.get(agent.id);
            const lastTs = parseTs(st?.last?.ts);
            const active = lastTs && now ? now - lastTs.getTime() < 15_000 : false;
            const realCount = st?.real ?? 0;
            const errCount = st?.errors ?? 0;
            return (
              <button
                key={agent.id}
                type="button"
                onClick={() => onSelectAgent(agent.id)}
                className="group flex flex-col rounded-xl border border-rule bg-surface p-4 text-left transition hover:border-act hover:shadow-sm"
              >
                <div className="flex w-full items-center gap-2.5">
                  <span
                    className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg text-white"
                    style={{ background: agent.color }}
                  >
                    {agent.icon}
                  </span>
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <span className="truncate font-bold text-ink group-hover:text-act">{agent.name}</span>
                      <span
                        className={`inline-block h-2 w-2 rounded-full ${active ? "beat" : ""}`}
                        style={{ background: active ? "#2f7a4d" : realCount > 0 ? agent.color : "#d3d9e1" }}
                        title={active ? "Active now" : st?.last ? `Last active ${ago(st.last.ts, now)}` : "Idle"}
                      />
                    </div>
                    <p className="truncate text-xs text-muted">{agent.role}</p>
                  </div>
                </div>

                <div className="mt-3 min-h-[36px] rounded-md bg-dock/70 px-3 py-2 text-xs text-ink">
                  {st?.last ? (
                    <span className="line-clamp-2">{st.last.detail || humanizeAction(st.last.action).label}</span>
                  ) : (
                    <span className="text-muted">Waiting for next drop or event…</span>
                  )}
                </div>

                <div className="mt-3 flex w-full items-center justify-between text-xs tabular">
                  <span className="text-muted">
                    <strong className="text-ink">{fmt(realCount)}</strong> actions · {st?.last ? ago(st.last.ts, now) : "idle"}
                  </span>
                  {errCount > 0 ? (
                    <span className="rounded bg-stop-soft px-2 py-0.5 font-semibold text-stop">{errCount} errors</span>
                  ) : (
                    <span className="font-semibold text-act opacity-0 transition group-hover:opacity-100">Open report →</span>
                  )}
                </div>

                <div className="mt-2 h-1.5 w-full rounded-full bg-dock" aria-hidden>
                  <div
                    className="h-1.5 rounded-full transition-all"
                    style={{ width: `${(realCount / maxReal) * 100}%`, background: agent.color }}
                  />
                </div>
              </button>
            );
          })}
        </div>
      </section>

      <StackedBar
        title="Workload Distribution Across Agents"
        segments={workers.map((w) => ({
          label: w.shortName,
          count: agentStats.get(w.id)?.real ?? 0,
          color: w.color,
        }))}
      />
    </div>
  );
}

// ---------------------------------------------------------------------------
// 2. Watcher Report
// ---------------------------------------------------------------------------
export function WatcherReport({ snap }: { snap: ObservabilitySnapshot }) {
  const totalAdded = snap.drops.reduce((a, d) => a + d.added.length, 0);
  const totalChanged = snap.drops.reduce((a, d) => a + d.changed.length, 0);
  const totalRemoved = snap.drops.reduce((a, d) => a + d.removed.length, 0);

  return (
    <div className="space-y-6">
      <KpiGrid
        items={[
          { label: "Portal Drops Detected", value: fmt(snap.drops.length), hint: "Each drop triggers parallel downstream agents", tone: "act" },
          { label: "Files Added", value: fmt(totalAdded), hint: "New ERP CSVs, notices, profiles & scans", tone: "ok" },
          { label: "Files Modified", value: fmt(totalChanged), hint: "Updated schemas or replaced files", tone: totalChanged > 0 ? "warn" : "default" },
          { label: "Files Removed", value: fmt(totalRemoved), hint: "Kept as historical provenance", tone: totalRemoved > 0 ? "stop" : "default" },
        ]}
      />

      {snap.drops.length === 0 ? (
        <EmptyState text="No portal drops processed yet. Click 'Play next data drop' above to replay real drops." />
      ) : (
        <div className="space-y-3">
          <h2 className="text-lg font-bold">Portal Drop Checksums & File Deltas</h2>
          {snap.drops.map((d) => (
            <details key={d.id} className="rounded-xl border border-rule bg-surface p-4" open={d.id === snap.drops[0]?.id}>
              <summary className="flex cursor-pointer flex-wrap items-center gap-3 list-none">
                <span className="rounded bg-dock px-2 py-0.5 text-xs font-bold uppercase text-muted">{d.mode}</span>
                <span className="font-bold text-ink">{d.label || `Drop #${d.id}`}</span>
                <span className="text-xs text-muted tabular">{clock(d.checked_at)}</span>
                <div className="ml-auto flex gap-2 text-xs font-semibold tabular">
                  <span className="rounded bg-ok-soft px-2 py-0.5 text-ok">+{d.added.length} added</span>
                  <span className="rounded bg-warn-soft px-2 py-0.5 text-warn">~{d.changed.length} changed</span>
                  {d.removed.length > 0 && (
                    <span className="rounded bg-stop-soft px-2 py-0.5 text-stop">-{d.removed.length} removed</span>
                  )}
                </div>
              </summary>
              <div className="mt-3 grid gap-3 border-t border-rule pt-3 text-xs md:grid-cols-2">
                {d.added.length > 0 && (
                  <div>
                    <p className="mb-1 font-semibold text-ok">Added files ({d.added.length})</p>
                    <ul className="max-h-40 space-y-0.5 overflow-y-auto text-muted">
                      {d.added.slice(0, 40).map((f) => (
                        <li key={f} className="truncate">{f}</li>
                      ))}
                      {d.added.length > 40 && <li className="italic">+{d.added.length - 40} more files…</li>}
                    </ul>
                  </div>
                )}
                {d.changed.length > 0 && (
                  <div>
                    <p className="mb-1 font-semibold text-warn">Changed files ({d.changed.length})</p>
                    <ul className="space-y-0.5 text-muted">
                      {d.changed.map((f) => (
                        <li key={f} className="truncate">{f}</li>
                      ))}
                    </ul>
                  </div>
                )}
              </div>
            </details>
          ))}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// 3. Ingest & Normalizer Report
// ---------------------------------------------------------------------------
export function IngestReport({ snap }: { snap: ObservabilitySnapshot }) {
  const ingestTrace = snap.trace.filter((e) => e.agent === "ingest");
  const errors = ingestTrace.filter((e) => e.action === "error").length;
  const fills = snap.changes.filter((c) => c.change_type === "fill").length;
  const overwrites = snap.changes.filter((c) => c.change_type === "overwrite").length;
  const tables = new Set(snap.changes.map((c) => c.table_name));

  return (
    <div className="space-y-6">
      <KpiGrid
        items={[
          { label: "Truth-Store Updates", value: fmt(snap.changes.length), hint: "Every change logged (no silent overwrites)", tone: "act" },
          { label: "Missing Fields Filled", value: fmt(fills), hint: "From supplier replies & delta files", tone: "ok" },
          { label: "Values Corrected", value: fmt(overwrites), hint: "Updated with full old → new provenance", tone: "warn" },
          { label: "Ingest Errors", value: fmt(errors), hint: `${fmt(tables.size)} tables updated by deltas`, tone: errors > 0 ? "stop" : "ok" },
        ]}
      />

      <StackedBar
        title="Truth-Store Change Breakdown"
        segments={[
          { label: "Filled empty fields", count: fills, color: "#2f7a4d" },
          { label: "Overwrites / corrections", count: overwrites, color: "#1d5fb4" },
        ]}
      />

      <section aria-label="Field change log">
        <h2 className="mb-3 text-lg font-bold">Audit Diff: What Ingest Changed in the Truth Store</h2>
        {snap.changes.length === 0 ? (
          <EmptyState text="No field diffs yet. Field changes appear when deltas, updated profiles, or supplier replies arrive." />
        ) : (
          <div className="overflow-x-auto rounded-xl border border-rule bg-surface">
            <table className="w-full text-left text-sm">
              <thead className="border-b border-rule text-xs uppercase text-muted">
                <tr>
                  <th className="px-4 py-2.5 font-semibold">Entity / Field</th>
                  <th className="px-4 py-2.5 font-semibold">Old → New Value</th>
                  <th className="px-4 py-2.5 font-semibold">Source & Reason</th>
                  <th className="px-4 py-2.5 font-semibold">Time</th>
                </tr>
              </thead>
              <tbody>
                {snap.changes.slice(0, 60).map((c) => (
                  <tr key={c.id} className="border-b border-rule last:border-b-0">
                    <td className="px-4 py-2.5 align-top">
                      <span className="font-semibold text-ink">{c.entity_key || `${c.table_name}#${c.row_id}`}</span>
                      <span className="block text-xs text-muted">{c.table_name}.{c.field}</span>
                    </td>
                    <td className="px-4 py-2.5 align-top">
                      <div className="flex flex-wrap items-center gap-1.5 text-xs">
                        <span className="rounded bg-dock px-2 py-0.5 line-through text-muted">{c.old_value || "empty"}</span>
                        <span aria-hidden>→</span>
                        <span className="rounded bg-ok-soft px-2 py-0.5 font-semibold text-ok">{c.new_value || "empty"}</span>
                      </div>
                    </td>
                    <td className="px-4 py-2.5 align-top text-xs">
                      <span className="font-medium text-ink">{c.source}</span>
                      {c.reason && <span className="block text-muted">{c.reason}</span>}
                    </td>
                    <td className="px-4 py-2.5 align-top text-xs text-muted tabular">{clock(c.ts)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}

// ---------------------------------------------------------------------------
// 4. Reconciler & Gate Report
// ---------------------------------------------------------------------------
export function ReconcilerReport({ snap }: { snap: ObservabilitySnapshot }) {
  const recIssues = snap.issues.filter((i) => i.raised_by === "reconciler");
  const open = recIssues.filter((i) => i.status !== "resolved");
  const resolved = recIssues.filter((i) => i.status === "resolved");
  const lineStops = open.filter((i) => i.severity >= 5);

  const byKind = {
    copies_at_risk: open.filter((i) => i.kind === "copies_at_risk").length,
    missing_info: open.filter((i) => i.kind === "missing_info").length,
    human_check: open.filter((i) => i.kind === "human_check").length,
  };

  return (
    <div className="space-y-6">
      <KpiGrid
        items={[
          { label: "Open Findings", value: fmt(open.length), hint: "Active traps & discrepancies", tone: open.length > 0 ? "warn" : "ok" },
          { label: "Line-Stoppers (Sev 5)", value: fmt(lineStops.length), hint: "Immediate launch blockers", tone: lineStops.length > 0 ? "stop" : "ok" },
          { label: "Auto-Resolved", value: fmt(resolved.length), hint: "Closed automatically by newer drops/replies", tone: "ok" },
          { label: "Source Conflicts", value: fmt(snap.conflicts.length), hint: `${fmt(snap.conflicts.filter((c) => c.status === "open").length)} currently open`, tone: "act" },
        ]}
      />

      <div className="grid gap-4 lg:grid-cols-2">
        <StackedBar
          title="Open Findings by Severity"
          segments={[
            { label: "5 · Line stops", count: open.filter((i) => i.severity === 5).length, color: "#b3261e" },
            { label: "4 · Copies at risk", count: open.filter((i) => i.severity === 4).length, color: "#d9534f" },
            { label: "3 · Answer today", count: open.filter((i) => i.severity === 3).length, color: "#a86a12" },
            { label: "2 · Watch", count: open.filter((i) => i.severity === 2).length, color: "#1d5fb4" },
            { label: "1 · Info", count: open.filter((i) => i.severity <= 1).length, color: "#5a6478" },
          ]}
        />
        <StackedBar
          title="Where Reconciler Routed Each Finding"
          segments={[
            { label: "Launch Planner (copies at risk)", count: byKind.copies_at_risk, color: "#127a7a" },
            { label: "Chaser (missing info)", count: byKind.missing_info, color: "#2f7a4d" },
            { label: "Human Gate (fraud / qty jumps)", count: byKind.human_check, color: "#16213a" },
          ]}
        />
      </div>

      {snap.conflicts.length > 0 && (
        <section aria-label="Source conflicts">
          <h2 className="mb-3 text-lg font-bold">Cross-Source Contradictions ({snap.conflicts.length})</h2>
          <div className="grid gap-3 md:grid-cols-2">
            {snap.conflicts.slice(0, 14).map((c: Conflict) => (
              <div key={c.id} className="rounded-xl border border-rule bg-surface p-4 text-sm">
                <div className="flex items-center justify-between gap-2">
                  <span className="font-bold text-ink">{c.entity_id} · {c.field}</span>
                  <span
                    className={`rounded px-2 py-0.5 text-xs font-semibold ${
                      c.status === "open" ? "bg-stop-soft text-stop" : "bg-ok-soft text-ok"
                    }`}
                  >
                    {c.status}
                  </span>
                </div>
                <div className="mt-2 grid grid-cols-2 gap-2 text-xs">
                  <div className="rounded bg-dock/70 p-2">
                    <span className="block font-semibold text-ink">{c.value_a || "—"}</span>
                    <span className="block truncate text-muted" title={c.source_a}>{c.source_a}</span>
                  </div>
                  <div className="rounded bg-warn-soft/70 p-2">
                    <span className="block font-semibold text-ink">{c.value_b || "—"}</span>
                    <span className="block truncate text-muted" title={c.source_b}>{c.source_b}</span>
                  </div>
                </div>
              </div>
            ))}
          </div>
        </section>
      )}

      <section aria-label="Reconciler findings">
        <h2 className="mb-3 text-lg font-bold">Detected Issues & Evidence ({recIssues.length})</h2>
        {recIssues.length === 0 ? (
          <EmptyState text="No Reconciler findings yet." />
        ) : (
          <div className="space-y-2.5">
            {recIssues
              .slice()
              .sort((a, b) => Number(a.status === "resolved") - Number(b.status === "resolved") || b.severity - a.severity)
              .map((iss) => (
                <details key={iss.id} className={`rounded-xl border border-rule bg-surface p-4 ${iss.status === "resolved" ? "opacity-60" : ""}`}>
                  <summary className="flex cursor-pointer flex-wrap items-center gap-2.5 list-none">
                    <span className={`rounded px-2 py-0.5 text-xs font-bold ${iss.severity >= 5 ? "bg-stop text-white" : iss.severity >= 4 ? "bg-stop-soft text-stop" : "bg-warn-soft text-warn"}`}>
                      Sev {iss.severity}
                    </span>
                    <span className="rounded bg-dock px-2 py-0.5 text-xs font-semibold text-ink">{iss.kind.replaceAll("_", " ")}</span>
                    <span className="min-w-0 flex-1 font-semibold text-ink">{iss.summary}</span>
                    <span className={`rounded px-2 py-0.5 text-xs font-semibold capitalize ${iss.status === "resolved" ? "bg-ok-soft text-ok" : "bg-act-soft text-act"}`}>
                      {iss.status}
                    </span>
                  </summary>
                  <div className="mt-3 border-t border-rule pt-3 text-sm">
                    {iss.reason && <p className="text-ink">{iss.reason}</p>}
                    {iss.suggested_actions.length > 0 && (
                      <div className="mt-2 flex flex-wrap gap-2">
                        {iss.suggested_actions.map((a, idx) => (
                          <span key={idx} className="rounded bg-act-soft px-2.5 py-1 text-xs font-semibold text-act">
                            {a.owner ? `[${a.owner}] ` : ""}{a.label}
                          </span>
                        ))}
                      </div>
                    )}
                    <EvidenceList evidence={iss.evidence} />
                  </div>
                </details>
              ))}
          </div>
        )}
      </section>
    </div>
  );
}

// ---------------------------------------------------------------------------
// 5. Chaser Report
// ---------------------------------------------------------------------------
export function ChaserReport({ snap }: { snap: ObservabilitySnapshot }) {
  const sent = snap.outbox.filter((m) => m.status === "sent" || m.status === "replied").length;
  const replied = snap.outbox.filter((m) => m.status === "replied").length;
  const escalated = snap.outbox.filter((m) => m.status === "escalated").length;
  const pending = snap.outbox.filter((m) => m.status === "draft" || m.status === "approved").length;

  return (
    <div className="space-y-6">
      <KpiGrid
        items={[
          { label: "Outbox Threads", value: fmt(snap.outbox.length), hint: `${fmt(pending)} waiting to send`, tone: "act" },
          { label: "Delivered / Simulated", value: fmt(sent), hint: "Rate-limited (never spams suppliers)", tone: "ok" },
          { label: "Replies Captured", value: fmt(replied), hint: `${fmt(snap.inbox.length)} inbox messages parsed`, tone: "ok" },
          { label: "Escalated on Silence", value: fmt(escalated), hint: "Sent to human buyer when unanswered", tone: escalated > 0 ? "warn" : "default" },
        ]}
      />

      <StackedBar
        title="Supplier Outreach Funnel"
        segments={[
          { label: "Replied", count: replied, color: "#2f7a4d" },
          { label: "Sent / awaiting reply", count: Math.max(0, sent - replied), color: "#1d5fb4" },
          { label: "Draft / approved", count: pending, color: "#a86a12" },
          { label: "Escalated", count: escalated, color: "#b3261e" },
        ]}
      />

      {snap.outbox.length === 0 ? (
        <EmptyState text="No supplier emails yet. Chaser triggers when Reconciler raises a missing-info finding or Planner executes an approved order." />
      ) : (
        <div className="space-y-3">
          <h2 className="text-lg font-bold">Multilingual Email Threads & Extracted Replies</h2>
          {snap.outbox.map((m) => {
            const replies = snap.inbox.filter((r) => r.outbox_id === m.id);
            return (
              <div key={m.id} className="rounded-xl border border-rule bg-surface p-4 text-sm">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="rounded bg-ink px-2 py-0.5 text-xs font-bold text-white uppercase">{m.language || "EN"}</span>
                  <span className="font-bold text-ink">{m.subject}</span>
                  <span className="text-xs text-muted">→ {m.supplier_id} ({m.to_email})</span>
                  <span className={`ml-auto rounded px-2 py-0.5 text-xs font-semibold capitalize ${m.status === "replied" ? "bg-ok-soft text-ok" : m.status === "escalated" ? "bg-stop-soft text-stop" : "bg-act-soft text-act"}`}>
                    {m.status}
                  </span>
                </div>
                <p className="mt-2 whitespace-pre-line rounded-md bg-dock/70 p-3 text-xs text-ink">
                  {m.body_en || m.body}
                </p>
                {replies.map((r) => (
                  <div key={r.id} className="mt-2 border-l-2 border-ok pl-3 text-xs">
                    <span className="font-semibold text-ok">Reply ({clock(r.received_at)}):</span>{" "}
                    <span className="text-ink">{r.body_en || r.body}</span>
                  </div>
                ))}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// 6. Launch Planner Report
// ---------------------------------------------------------------------------
export function PlannerReport({ snap }: { snap: ObservabilitySnapshot }) {
  const newestEditions = useMemo(() => {
    const m = new Map<string, Forecast>();
    for (const f of snap.forecast) if (!m.has(f.edition)) m.set(f.edition, f);
    return [...m.values()];
  }, [snap.forecast]);

  const proposed = snap.decisions.filter((d) => d.status === "proposed").length;
  const approved = snap.decisions.filter((d) => d.status === "approved" || d.status === "executed").length;
  const rejected = snap.decisions.filter((d) => d.status === "rejected").length;
  const outdated = snap.decisions.filter((d) => d.status === "outdated").length;

  return (
    <div className="space-y-6">
      <KpiGrid
        items={[
          { label: "Proposed Cards", value: fmt(proposed), hint: "Waiting for 1-click executive sign-off", tone: proposed > 0 ? "warn" : "ok" },
          { label: "Approved / Executed", value: fmt(approved), hint: "Triggered recovery actions", tone: "ok" },
          { label: "Rejected by Human", value: fmt(rejected), hint: "Tracked as human override signal", tone: rejected > 0 ? "stop" : "default" },
          { label: "Superseded (Outdated)", value: fmt(outdated), hint: "Recomputed when a newer drop arrived", tone: "default" },
        ]}
      />

      {newestEditions.length > 0 && (
        <section aria-label="Pack forecasts">
          <h2 className="mb-3 text-lg font-bold">Live Packing Forecast by Edition</h2>
          <div className="grid gap-4 md:grid-cols-2">
            {newestEditions.map((f) => {
              const still = f.still_to_pack ?? 0;
              const short = f.shortfall ?? 0;
              const packed = Math.max(0, still - short);
              const pct = still > 0 ? Math.min(100, Math.round((packed / still) * 100)) : 100;
              return (
                <div key={f.edition} className="rounded-xl border border-rule bg-surface p-4">
                  <div className="flex items-baseline justify-between">
                    <h3 className="text-base font-bold">{f.edition}</h3>
                    <span className={`text-sm font-bold tabular ${short > 0 ? "text-stop" : "text-ok"}`}>
                      {short > 0 ? `${fmt(short)} short` : "On track (0 short)"}
                    </span>
                  </div>
                  <div className="mt-2 h-3 w-full overflow-hidden rounded-full bg-stop-soft">
                    <div
                      className={`h-3 rounded-full ${short > 0 ? "bg-act" : "bg-ok"}`}
                      style={{ width: `${pct}%` }}
                    />
                  </div>
                  <p className="mt-2 text-xs text-muted tabular">
                    {fmt(packed)} / {fmt(still)} projected packed before close ({pct}%)
                    {f.limiting_part ? ` · Limiting part: ${f.limiting_part}` : ""}
                  </p>
                </div>
              );
            })}
          </div>
        </section>
      )}

      <section aria-label="Decision cards">
        <h2 className="mb-3 text-lg font-bold">Decision Cards Generated ({snap.decisions.length})</h2>
        {snap.decisions.length === 0 ? (
          <EmptyState text="No decision cards created yet." />
        ) : (
          <div className="space-y-3">
            {snap.decisions.slice(0, 25).map((d) => {
              const rec = d.chosen_option ?? d.recommended;
              const opt = rec != null ? d.options[rec] : undefined;
              return (
                <div key={d.id} className="rounded-xl border border-rule bg-surface p-4 text-sm">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-bold text-ink">{d.summary || d.title}</span>
                    <span className={`ml-auto rounded px-2 py-0.5 text-xs font-semibold capitalize ${d.status === "approved" || d.status === "executed" ? "bg-ok-soft text-ok" : d.status === "rejected" ? "bg-stop-soft text-stop" : d.status === "proposed" ? "bg-act-soft text-act" : "bg-dock text-muted"}`}>
                      {d.status}
                    </span>
                  </div>
                  <p className="mt-1 text-xs text-muted">
                    Approver: <strong className="text-ink">{d.approver || "auto"}</strong>
                    {d.approver_reason ? ` (${d.approver_reason})` : ""}
                    {d.decided_by ? ` · Decided by ${d.decided_by}` : ""}
                  </p>
                  {opt && (
                    <div className="mt-2 flex flex-wrap gap-3 rounded bg-dock/70 px-3 py-2 text-xs tabular">
                      <span className="font-semibold text-ink">{d.chosen_option != null ? "Chosen:" : "Recommended:"} {opt.label}</span>
                      <span>Cost: <strong>{eur(opt.cost_eur)}</strong></span>
                      <span>Gains: <strong>{fmt(opt.copies_gained)} copies</strong></span>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </section>
    </div>
  );
}

// ---------------------------------------------------------------------------
// 7. Risk Rater Report (3-Tier Moody's + 5 Live External Feeds)
// ---------------------------------------------------------------------------
export function RiskRaterReport({ snap }: { snap: ObservabilitySnapshot }) {
  const [filter, setFilter] = useState<"all" | "product" | "supplier" | "part">("all");

  const latest = useMemo(() => {
    const map = new Map<string, RiskScore>();
    for (const r of snap.risk) {
      const k = `${String(r.entity_type)}/${r.entity_id}`;
      if ((map.get(k)?.id ?? -1) < r.id) map.set(k, r);
    }
    return [...map.values()].sort((a, b) => b.grade - a.grade || a.entity_id.localeCompare(b.entity_id));
  }, [snap.risk]);

  const latestRiskLog = snap.trace.find((e) => e.agent === "risk" && e.action === "risk_rated");
  const extCounts = (latestRiskLog?.data?.external_signals as Record<string, number> | undefined) ?? {};

  const critical = latest.filter((r) => r.grade >= 6).length;
  const high = latest.filter((r) => r.grade === 5).length;
  const products = latest.filter((r) => String(r.entity_type) === "product").length;
  const extTotal = Object.values(extCounts).reduce((a, b) => a + Number(b || 0), 0);

  const visible = latest.filter((r) => filter === "all" || String(r.entity_type) === filter);

  return (
    <div className="space-y-6">
      <KpiGrid
        items={[
          { label: "Critical (CCC · 6/6)", value: fmt(critical), hint: "Immediate line-stop or ban impact", tone: critical > 0 ? "stop" : "ok" },
          { label: "High Risk (B · 5/6)", value: fmt(high), hint: "Finding auto-sent to Planner", tone: high > 0 ? "warn" : "ok" },
          { label: "Products / Parts / Suppliers", value: `${products} / ${latest.filter((r) => r.entity_type === "part").length} / ${latest.filter((r) => r.entity_type === "supplier").length}`, hint: "3-tier bottom-up Moody's rollup", tone: "act" },
          { label: "External & Reg Signals", value: fmt(extTotal), hint: "Polymarket · Open-Meteo · News · GPS · Regs", tone: "act" },
        ]}
      />

      {Object.keys(extCounts).length > 0 && (
        <div className="flex flex-wrap items-center gap-2 rounded-xl border border-rule bg-surface px-4 py-3 text-xs">
          <span className="font-bold text-ink">Active World & Regulatory Feeds:</span>
          {Object.entries(extCounts).map(([name, count]) => (
            <span key={name} className="rounded bg-act-soft px-2.5 py-1 font-semibold text-act tabular">
              {name}: {count} signals
            </span>
          ))}
        </div>
      )}

      <RiskPlaybook />

      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="text-lg font-bold">Moody&apos;s Risk Scorecard ({visible.length})</h2>
        <div className="flex gap-1 rounded-lg border border-rule bg-surface p-1 text-xs font-semibold">
          {(["all", "product", "supplier", "part"] as const).map((k) => (
            <button
              key={k}
              type="button"
              onClick={() => setFilter(k)}
              className={`rounded-md px-3 py-1 capitalize ${filter === k ? "bg-ink text-white" : "text-muted hover:text-ink"}`}
            >
              {k === "all" ? "All tiers" : `${k}s`}
            </button>
          ))}
        </div>
      </div>

      {visible.length === 0 ? (
        <EmptyState text="No risk scores computed yet." />
      ) : (
        <div className="grid gap-3 md:grid-cols-2">
          {visible.map((r) => {
            const reasons = (r.reasons ?? []) as ExtendedRiskReason[];
            const score = reasons[0]?.score_0_100 ?? Math.round((r.grade / 6) * 100);
            const breakdown = reasons[0]?.breakdown ?? {};
            const barColor = r.grade >= 6 ? "#b3261e" : r.grade >= 5 ? "#a86a12" : r.grade >= 4 ? "#1d5fb4" : "#2f7a4d";
            return (
              <details key={`${String(r.entity_type)}-${r.entity_id}`} className="rounded-xl border border-rule bg-surface p-4" open={r.grade >= 5}>
                <summary className="flex cursor-pointer items-center gap-3 list-none">
                  <span
                    className={`flex h-11 w-14 shrink-0 flex-col items-center justify-center rounded-lg font-bold tabular ${
                      r.grade >= 6 ? "bg-stop text-white" : r.grade >= 5 ? "bg-stop-soft text-stop" : r.grade >= 4 ? "bg-warn-soft text-warn" : "bg-ok-soft text-ok"
                    }`}
                  >
                    <span className="text-sm leading-none">{r.rating}</span>
                    <span className="mt-0.5 text-[10px] opacity-80">{r.grade}/6</span>
                  </span>
                  <div className="min-w-0 flex-1">
                    <div className="flex items-baseline justify-between gap-2">
                      <span className="truncate font-bold text-ink">{r.entity_id}</span>
                      <span className="text-xs font-semibold text-muted tabular">{Math.round(score)}/100</span>
                    </div>
                    <span className="block text-xs uppercase tracking-wide text-muted">{String(r.entity_type)}</span>
                    <div className="mt-1.5 h-1.5 w-full rounded-full bg-dock" aria-hidden>
                      <div className="h-1.5 rounded-full" style={{ width: `${Math.min(100, score)}%`, background: barColor }} />
                    </div>
                  </div>
                </summary>

                <div className="mt-3 space-y-2 border-t border-rule pt-3 text-xs">
                  <p className="font-semibold uppercase tracking-wide text-muted">Top reasons & evidence</p>
                  {reasons.map((rs, idx) => (
                    <div key={idx} className="rounded-md bg-dock/60 p-2.5">
                      <div className="flex items-start justify-between gap-2">
                        <span className="font-medium text-ink">{rs.text}</span>
                        {rs.points != null && rs.points > 0 && (
                          <span className="shrink-0 rounded bg-surface px-1.5 py-0.5 font-bold text-ink tabular">
                            +{Math.round(rs.points)} pts
                          </span>
                        )}
                      </div>
                      {rs.evidence && rs.evidence.length > 0 && <EvidenceList evidence={rs.evidence} />}
                    </div>
                  ))}
                  {Object.keys(breakdown).length > 0 && (
                    <div className="flex flex-wrap gap-1.5 pt-1">
                      {Object.entries(breakdown)
                        .filter(([, v]) => v > 0)
                        .map(([k, v]) => (
                          <span key={k} className="rounded border border-rule px-2 py-0.5 text-[11px] text-muted tabular">
                            {k.replaceAll("_", " ")}: <strong className="text-ink">{v}</strong>
                          </span>
                        ))}
                    </div>
                  )}
                </div>
              </details>
            );
          })}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// 8. Human Audit Report
// ---------------------------------------------------------------------------
export function HumanAuditReport({ snap }: { snap: ObservabilitySnapshot }) {
  const decided = snap.decisions.filter((d) => d.status === "approved" || d.status === "rejected" || d.status === "executed");
  const approved = snap.decisions.filter((d) => d.status === "approved" || d.status === "executed").length;
  const rejected = snap.decisions.filter((d) => d.status === "rejected").length;
  const pending = snap.decisions.filter((d) => d.status === "proposed").length;
  const humanChecks = snap.issues.filter((i) => i.kind === "human_check" && i.status !== "resolved").length;

  return (
    <div className="space-y-6">
      <KpiGrid
        items={[
          { label: "Total Human Decisions", value: fmt(decided.length), hint: "Full executive audit log", tone: "act" },
          { label: "Approved Actions", value: fmt(approved), hint: "Released for execution", tone: "ok" },
          { label: "Rejected / Overridden", value: fmt(rejected), hint: "Agent proposals declined by human", tone: rejected > 0 ? "stop" : "default" },
          { label: "Pending Human Checks", value: fmt(pending + humanChecks), hint: `${pending} cards + ${humanChecks} fraud/qty gates`, tone: pending + humanChecks > 0 ? "warn" : "ok" },
        ]}
      />

      {decided.length === 0 ? (
        <EmptyState text="No human approvals or rejections recorded yet. Approve or reject a card in Control Room to see the audit record." />
      ) : (
        <div className="space-y-3">
          <h2 className="text-lg font-bold">Executive Sign-Off Ledger</h2>
          {decided.map((d) => (
            <div key={d.id} className="rounded-xl border border-rule bg-surface p-4 text-sm">
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-bold text-ink">{d.summary || d.title}</span>
                <span className={`ml-auto rounded px-2 py-0.5 text-xs font-semibold capitalize ${d.status === "rejected" ? "bg-stop-soft text-stop" : "bg-ok-soft text-ok"}`}>
                  {d.status}
                </span>
              </div>
              <p className="mt-1 text-xs text-muted">
                Signed by <strong className="text-ink">{d.decided_by || d.approver || "unknown"}</strong>
                {d.decided_at ? ` at ${clock(d.decided_at)}` : ""}
                {d.chosen_option != null && d.options[d.chosen_option] ? ` · Option: ${d.options[d.chosen_option].label}` : ""}
              </p>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// 9. Shared Activity Timeline (rendered on every agent view)
// ---------------------------------------------------------------------------
export function AgentTimeline({
  trace,
  agentId,
  now,
}: {
  trace: AgentLogEntry[];
  agentId: AgentId;
  now: number;
}) {
  const [filter, setFilter] = useState<"key" | "errors" | "all">("key");

  const scoped = useMemo(() => {
    const base = agentId === "overview" ? trace : trace.filter((e) => e.agent === agentId);
    if (filter === "errors") return base.filter((e) => e.action === "error");
    if (filter === "key") return base.filter((e) => !isPlumbingEvent(e));
    return base;
  }, [trace, agentId, filter]);

  return (
    <section aria-label="Activity timeline" className="rounded-xl border border-rule bg-surface p-4">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-base font-bold">
            {agentId === "overview" ? "Cross-Agent Activity Stream" : `${agentMeta(agentId).name} Activity Log`}
          </h2>
          <p className="text-xs text-muted">Plain-English trace of every action, decision, and error ({scoped.length} entries)</p>
        </div>
        <div className="flex gap-1 rounded-lg border border-rule bg-dock p-1 text-xs font-semibold">
          {(
            [
              ["key", "Key events"],
              ["errors", "Errors only"],
              ["all", "All incl. hand-offs"],
            ] as const
          ).map(([id, label]) => (
            <button
              key={id}
              type="button"
              onClick={() => setFilter(id)}
              className={`rounded-md px-2.5 py-1 ${filter === id ? "bg-surface text-ink shadow-xs" : "text-muted hover:text-ink"}`}
            >
              {label}
            </button>
          ))}
        </div>
      </div>

      {scoped.length === 0 ? (
        <p className="py-6 text-center text-sm text-muted">
          {filter === "errors" ? "Zero errors recorded — clean run." : "No logged events for this view yet."}
        </p>
      ) : (
        <ol className="max-h-[420px] divide-y divide-rule overflow-y-auto text-sm">
          {scoped.slice(0, 80).map((e) => {
            const meta = agentMeta(e.agent);
            const h = humanizeAction(e.action);
            return (
              <li key={e.id} className="py-2.5 first:pt-0 last:pb-0">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-xs text-muted tabular" title={ago(e.ts, now)}>{clock(e.ts)}</span>
                  <span
                    className="rounded px-2 py-0.5 text-xs font-semibold text-white"
                    style={{ background: meta.color }}
                  >
                    {meta.shortName}
                  </span>
                  <span className={`rounded px-2 py-0.5 text-xs font-semibold ${TONE_CLASS[h.tone]}`}>{h.label}</span>
                  {e.run_id && <span className="ml-auto font-mono text-[11px] text-muted">run {e.run_id.slice(0, 6)}</span>}
                </div>
                {e.detail && <p className="mt-1 break-words text-sm text-ink">{e.detail}</p>}
                {e.evidence && e.evidence.length > 0 && <EvidenceList evidence={e.evidence} />}
              </li>
            );
          })}
        </ol>
      )}
    </section>
  );
}
