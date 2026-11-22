"use client";

import { useEffect, useMemo, useState } from "react";
import { AgentRoster } from "@/components/ui/agent-roster";
import {
  ago, clock, eur, fmt, getData,
  type AgentLogEntry, type Conflict, type Evidence, type ExtendedRiskReason,
  type Forecast, type ObservabilitySnapshot, type RiskScore,
} from "@/lib/api";
import {
  AGENT_NAV, TONE_CLASS, agentMeta, entryDetail, humanizeAction, isPlumbingEvent, type AgentId,
} from "./agent-meta";
import RiskPlaybook from "../risk-playbook";
import { ForecastPanel } from "./forecast-chart";
import { GRADE_COLOR, plainRisks, RiskCard } from "./risk-cards";
import { buildRosterAgents, plural } from "./roster";

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
          {/* Number first, then a short label, like the tiles on the control room. */}
          <p className={`figure text-4xl ${KPI_COLOR[item.tone ?? "default"]}`}>{item.value}</p>
          <p className="mt-2 text-sm font-semibold text-ink">{item.label}</p>
          {item.hint && <p className="mt-0.5 text-xs text-muted">{item.hint}</p>}
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
  online = null,
  onSelectAgent,
}: {
  snap: ObservabilitySnapshot;
  now: number;
  online?: boolean | null;
  onSelectAgent: (id: AgentId) => void;
}) {
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
  const roster = useMemo(() => buildRosterAgents(snap, now, online), [snap, now, online]);

  return (
    <div className="space-y-6">
      {/* What happens next, not how busy the agents were: the Planner's hour-by-hour packing forecast. */}
      <ForecastPanel forecasts={snap.forecast} now={now} />

      <section aria-label="Agent fleet" className="rounded-xl border border-rule bg-surface p-4 sm:p-5">
        <AgentRoster
          agents={roster}
          title="Agents · pick one to open its report"
          onSelectedChange={(id) => {
            if (id) onSelectAgent(id as AgentId);
          }}
        />
      </section>

      <StackedBar
        title="Actions per agent"
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
          { label: "Data drops", value: fmt(snap.drops.length), hint: snap.drops[0] ? `last at ${clock(snap.drops[0].checked_at)}` : undefined, tone: "act" },
          { label: "Files added", value: fmt(totalAdded), tone: "ok" },
          { label: "Files changed", value: fmt(totalChanged), tone: totalChanged > 0 ? "warn" : "default" },
          { label: "Files removed", value: fmt(totalRemoved), hint: "Old copies kept", tone: totalRemoved > 0 ? "stop" : "default" },
        ]}
      />

      {snap.drops.length === 0 ? (
        <EmptyState text="No portal drops processed yet. Click 'Play next data drop' above to replay real drops." />
      ) : (
        <div className="space-y-3">
          <h2 className="text-lg font-bold">Drops</h2>
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
          { label: "Changes logged", value: fmt(snap.changes.length), hint: "Each with old → new", tone: "act" },
          { label: "Empty fields filled", value: fmt(fills), hint: "From replies and new files", tone: "ok" },
          { label: "Values corrected", value: fmt(overwrites), tone: "warn" },
          { label: "Errors", value: fmt(errors), hint: `${plural(tables.size, "table")} updated`, tone: errors > 0 ? "stop" : "ok" },
        ]}
      />

      <StackedBar
        title="Changes by type"
        segments={[
          { label: "Filled empty fields", count: fills, color: "var(--color-ok)" },
          { label: "Corrected values", count: overwrites, color: "var(--primary)" },
        ]}
      />

      <section aria-label="Field change log">
        <h2 className="mb-3 text-lg font-bold">What changed (old → new)</h2>
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
          { label: "Open findings", value: fmt(open.length), tone: open.length > 0 ? "warn" : "ok" },
          { label: "Urgent", value: fmt(lineStops.length), hint: "The line would stop", tone: lineStops.length > 0 ? "stop" : "ok" },
          { label: "Closed on their own", value: fmt(resolved.length), hint: "By a newer drop or reply", tone: "ok" },
          { label: "Source conflicts", value: fmt(snap.conflicts.length), hint: `${fmt(snap.conflicts.filter((c) => c.status === "open").length)} open`, tone: "act" },
        ]}
      />

      <div className="grid gap-4 lg:grid-cols-2">
        <StackedBar
          title="Open findings by severity"
          segments={[
            { label: "Urgent", count: open.filter((i) => i.severity === 5).length, color: "var(--signal-red)" },
            { label: "High", count: open.filter((i) => i.severity === 4).length, color: "color-mix(in srgb, var(--signal-red) 70%, transparent)" },
            { label: "Today", count: open.filter((i) => i.severity === 3).length, color: "var(--signal-amber)" },
            { label: "Watch", count: open.filter((i) => i.severity === 2).length, color: "var(--signal-green)" },
            { label: "Info", count: open.filter((i) => i.severity <= 1).length, color: "var(--border)" },
          ]}
        />
        <StackedBar
          title="Where each finding went"
          segments={[
            { label: "Planner · copies at risk", count: byKind.copies_at_risk, color: "var(--color-act)" },
            { label: "Chaser · missing info", count: byKind.missing_info, color: "var(--accent)" },
            { label: "Human check · fraud, big jumps", count: byKind.human_check, color: "var(--color-muted)" },
          ]}
        />
      </div>

      {snap.conflicts.length > 0 && (
        <section aria-label="Source conflicts">
          <h2 className="mb-3 text-lg font-bold">Sources that disagree ({snap.conflicts.length})</h2>
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
        <h2 className="mb-3 text-lg font-bold">Findings ({recIssues.length})</h2>
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
          { label: "Emails", value: fmt(snap.outbox.length), hint: `${fmt(pending)} waiting to send`, tone: "act" },
          { label: "Sent", value: fmt(sent), hint: "Rate-limited per supplier", tone: "ok" },
          { label: "Replies", value: fmt(replied), hint: `${plural(snap.inbox.length, "inbox message")} read`, tone: "ok" },
          { label: "Escalated", value: fmt(escalated), hint: "No reply, handed to the buyer", tone: escalated > 0 ? "warn" : "default" },
        ]}
      />

      <StackedBar
        title="Supplier emails"
        segments={[
          { label: "Replied", count: replied, color: "var(--color-ok)" },
          { label: "Waiting for reply", count: Math.max(0, sent - replied), color: "var(--primary)" },
          { label: "Not sent yet", count: pending, color: "var(--color-warn)" },
          { label: "Escalated", count: escalated, color: "var(--color-stop)" },
        ]}
      />

      {snap.outbox.length === 0 ? (
        <EmptyState text="No supplier emails yet. Chaser triggers when Reconciler raises a missing-info finding or Planner executes an approved order." />
      ) : (
        <div className="space-y-3">
          <h2 className="text-lg font-bold">Emails and replies</h2>
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
          { label: "Waiting for approval", value: fmt(proposed), tone: proposed > 0 ? "warn" : "ok" },
          { label: "Approved", value: fmt(approved), tone: "ok" },
          { label: "Rejected", value: fmt(rejected), tone: rejected > 0 ? "stop" : "default" },
          { label: "Outdated", value: fmt(outdated), hint: "Replaced after a newer drop", tone: "default" },
        ]}
      />

      {newestEditions.length > 0 && (
        <section aria-label="Pack forecasts">
          <h2 className="mb-3 text-lg font-bold">Forecast by edition</h2>
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
        <h2 className="mb-3 text-lg font-bold">Decision cards ({snap.decisions.length})</h2>
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
// 7. Risk Rater Report (ratings AAA-CCC for editions, parts and suppliers, plus outside feeds)
// ---------------------------------------------------------------------------
export function RiskRaterReport({ snap }: { snap: ObservabilitySnapshot }) {
  const mock = snap.health?.mockMode ?? true;
  const [filter, setFilter] = useState<"all" | "product" | "supplier" | "part">("all");
  const [names, setNames] = useState<Record<string, string>>({});

  // Supplier and part names for the cards (best effort; the cards fall back to ids).
  useEffect(() => {
    let alive = true;
    Promise.all([
      getData<{ component_id: string; description: string }[]>("/api/truth/materials").catch(() => []),
      getData<{ supplier_id: string; name: string }[]>("/api/truth/suppliers").catch(() => []),
    ]).then(([materials, suppliers]) => {
      if (!alive) return;
      setNames(Object.fromEntries([
        ...materials.map((m) => [m.component_id, m.description.split(" – ")[0]] as const),
        ...suppliers.map((x) => [x.supplier_id, x.name] as const),
      ]));
    });
    return () => {
      alive = false;
    };
  }, []);

  const latest = useMemo(() => {
    const map = new Map<string, RiskScore>();
    for (const r of snap.risk) {
      const k = `${String(r.entity_type)}/${r.entity_id}`;
      if ((map.get(k)?.id ?? -1) < r.id) map.set(k, r);
    }
    return [...map.values()].sort((a, b) => b.grade - a.grade || a.entity_id.localeCompare(b.entity_id));
  }, [snap.risk]);

  const editions = useMemo(() => {
    const out: Record<string, string> = {};
    for (const f of snap.forecast) out[f.product_id] ??= `${f.edition} edition`;
    return out;
  }, [snap.forecast]);
  const cards = useMemo(() => plainRisks(latest, names, editions), [latest, names, editions]);

  const none = latest.length === 0;
  const at = (type: string) => cards.filter((c) => c.type === type && c.grade >= 5);
  const riskySuppliers = at("supplier");
  const riskyParts = at("part");
  const riskyEditions = at("product");
  const allSources = [...new Map(cards.flatMap((c) => c.sources).map((x) => [x.key, x])).values()];
  const soony = allSources.filter((x) => x.kind === "soony").length;
  const outside = allSources.filter((x) => x.kind === "live" || x.kind === "example").length;
  const offline = allSources.filter((x) => x.kind === "example").length;

  // What drives supplier risk: the Risk Rater's own score breakdown per supplier, grouped by where the signal comes from.
  const drivers = { status: 0, approvals: 0, news: 0, markets: 0, rules: 0, route: 0, other: 0 };
  for (const r of latest.filter((x) => String(x.entity_type) === "supplier")) {
    const breakdown = ((r.reasons ?? [])[0] as ExtendedRiskReason | undefined)?.breakdown ?? {};
    for (const [k, v] of Object.entries(breakdown)) {
      const pts = Number(v) || 0;
      if (k === "supplier_health") drivers.status += pts;
      else if (k === "internal_politics") drivers.approvals += pts;
      else if (k === "trade_news") drivers.news += pts;
      else if (k === "external_politics") drivers.markets += pts;
      else if (k === "regulatory_change") drivers.rules += pts;
      else if (k === "weather" || k === "telemetry" || k === "transport") drivers.route += pts;
      else drivers.other += pts;
    }
  }
  const sample = mock ? " (offline snapshot)" : "";
  const grades = [6, 5, 4, 3, 2, 1].map((g) => ({ g, n: latest.filter((r) => r.grade === g).length }));
  const RATING = ["", "AAA", "AA", "A", "BBB", "B", "CCC"];

  const visible = cards.filter((c) => filter === "all" || c.type === filter);

  return (
    <div className="space-y-6">
      <KpiGrid
        items={[
          { label: "High-risk suppliers", value: fmt(riskySuppliers.length),
            hint: none ? "Not rated yet" : riskySuppliers[0] ? `${riskySuppliers[0].name} rated ${riskySuppliers[0].rating}` : "None rated B or worse",
            tone: none ? "default" : riskySuppliers.length ? "stop" : "ok" },
          { label: "Parts at risk", value: fmt(riskyParts.length),
            hint: none ? "Not rated yet" : `of ${fmt(cards.filter((c) => c.type === "part").length)} parts rated`, tone: none ? "default" : riskyParts.length ? "warn" : "ok" },
          { label: "Editions at risk", value: fmt(riskyEditions.length),
            hint: none ? "Not rated yet" : riskyEditions.length ? riskyEditions.map((c) => c.name).join(" · ") : `${fmt(Object.keys(editions).length)} editions on track`,
            tone: none ? "default" : riskyEditions.length ? "warn" : "ok" },
          { label: "Sources behind the ratings", value: fmt(soony + outside),
            hint: none ? "Not rated yet" : `${fmt(soony)} Soony files · ${fmt(outside)} outside feeds${offline ? ` (${fmt(offline)} offline snapshots)` : ""}`, tone: "act" },
        ]}
      />

      <div className="grid gap-4 lg:grid-cols-2">
        <StackedBar
          title="Ratings by grade"
          segments={grades.filter((x) => x.n > 0).map((x) => ({ label: RATING[x.g], count: x.n, color: GRADE_COLOR[x.g] }))}
        />
        <StackedBar
          title="What drives supplier risk (points)"
          segments={[
            { label: "Supplier status and notices", count: Math.round(drivers.status), color: "var(--color-stop)" },
            { label: "Soony portal news", count: Math.round(drivers.news), color: "var(--primary)" },
            { label: "Approvals and open checks", count: Math.round(drivers.approvals), color: "var(--color-muted)" },
            { label: `Weather and satellite tracking`, count: Math.round(drivers.route), color: "var(--accent)" },
            { label: `Markets (Polymarket${sample})`, count: Math.round(drivers.markets), color: "var(--color-warn)" },
            { label: `New rules${sample}`, count: Math.round(drivers.rules), color: "color-mix(in srgb, var(--color-warn) 45%, white)" },
            { label: "Other", count: Math.round(drivers.other), color: "var(--color-rule)" },
          ].filter((x) => x.count > 0)}
        />
      </div>

      <section aria-label="Risk ratings">
        <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
          <h2 className="text-lg font-bold">Risk ratings ({visible.length})</h2>
          <div className="flex gap-1 rounded-lg border border-rule bg-surface p-1 text-xs font-semibold">
            {(["all", "supplier", "part", "product"] as const).map((k) => (
              <button
                key={k}
                type="button"
                onClick={() => setFilter(k)}
                className={`rounded-md px-3 py-1 ${filter === k ? "bg-primary text-primary-foreground" : "text-muted hover:text-ink"}`}
              >
                {k === "all" ? "All" : k === "product" ? "Editions" : `${k[0].toUpperCase()}${k.slice(1)}s`}
              </button>
            ))}
          </div>
        </div>
        {visible.length === 0 ? (
          <EmptyState text="No risk ratings yet." />
        ) : (
          <div className="grid gap-3 md:grid-cols-2">
            {visible.map((c) => <RiskCard key={c.key} r={c} />)}
          </div>
        )}
      </section>

      <RiskPlaybook mock={mock} />
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
          { label: "Decisions", value: fmt(decided.length), tone: "act" },
          { label: "Approved", value: fmt(approved), tone: "ok" },
          { label: "Rejected", value: fmt(rejected), tone: rejected > 0 ? "stop" : "default" },
          { label: "Waiting", value: fmt(pending + humanChecks), hint: `${plural(pending, "card")} · ${plural(humanChecks, "check")}`, tone: pending + humanChecks > 0 ? "warn" : "ok" },
        ]}
      />

      {decided.length === 0 ? (
        <EmptyState text="No human approvals or rejections recorded yet. Approve or reject a card in Control Room to see the audit record." />
      ) : (
        <div className="space-y-3">
          <h2 className="text-lg font-bold">Who decided what</h2>
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
            {agentId === "overview" ? "Activity" : `${agentMeta(agentId).name} activity`}
          </h2>
          <p className="text-xs text-muted tabular">{plural(scoped.length, "entry", "entries")}</p>
        </div>
        <div className="flex gap-1 rounded-lg border border-rule bg-dock p-1 text-xs font-semibold">
          {(
            [
              ["key", "Key events"],
              ["errors", "Errors"],
              ["all", "All"],
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
            const detail = entryDetail(e);
            return (
              <li key={e.id} className="py-2.5 first:pt-0 last:pb-0">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-xs text-muted tabular" title={ago(e.ts, now)}>{clock(e.ts)}</span>
                  <span className="inline-flex items-center gap-1.5 rounded bg-secondary px-2 py-0.5 text-xs font-semibold text-ink">
                    <span aria-hidden className="h-1.5 w-1.5 rounded-full" style={{ background: meta.color }} />
                    {meta.shortName}
                  </span>
                  <span className={`rounded px-2 py-0.5 text-xs font-semibold ${TONE_CLASS[h.tone]}`}>{h.label}</span>
                  {e.run_id && <span className="ml-auto font-mono text-[11px] text-muted">run {e.run_id.slice(0, 6)}</span>}
                </div>
                {detail && <p className="mt-1 break-words text-sm text-ink">{detail}</p>}
                {e.evidence && e.evidence.length > 0 && <EvidenceList evidence={e.evidence} />}
              </li>
            );
          })}
        </ol>
      )}
    </section>
  );
}
