"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { findingHref, mailHref } from "../buyer-links";
import { useEffect, useMemo, useState } from "react";
import { AgentRoster } from "@/components/ui/agent-roster";
import {
  ago, clock, eur, fmt, getData, parseTs,
  type AgentLogEntry, type Conflict, type Evidence,
  type ObservabilitySnapshot, type RiskScore,
} from "@/lib/api";
import {
  AGENT_NAV, TONE_CLASS, agentMeta, entryDetail, humanizeAction, isPlumbingEvent, type AgentId,
} from "./agent-meta";
import RiskPlaybook from "../risk-playbook";
import { SupplySignals } from "./supply-signals";
import { RatingReasons, RatingReasonSummary } from "./rating-reasons";
import { recordedScore, riskBands, type RiskNames } from "./risk-explanations";
import { ForecastPanel } from "./forecast-chart";
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
        const isUrl = /^https?:\/\//i.test(e.path) && !/\boffline\b/i.test(e.quote) && e.source_kind !== "Offline example";
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
}: {
  snap: ObservabilitySnapshot;
  now: number;
  online?: boolean | null;
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

  const workers = AGENT_NAV.filter((a) => a.id !== "overview" && a.id !== "human");
  const roster = useMemo(() => buildRosterAgents(snap, now, online), [snap, now, online]);

  return (
    <div className="space-y-6">
      <section aria-label="Agent reports" className="rounded-xl border border-rule bg-surface p-4 sm:p-5">
        <AgentRoster agents={roster} title="Latest work by agent" />
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
              <div className="mt-3 grid grid-cols-1 gap-3 border-t border-rule pt-3 text-xs md:grid-cols-2">
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
          { label: "Filled empty fields", count: fills, color: "#2f7a4d" },
          { label: "Corrected values", count: overwrites, color: "#1d5fb4" },
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
            { label: "Planner · copies at risk", count: byKind.copies_at_risk, color: "#127a7a" },
            { label: "Chaser · missing info", count: byKind.missing_info, color: "#2f7a4d" },
            { label: "Human check · fraud, big jumps", count: byKind.human_check, color: "#16213a" },
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
                    <Link href={findingHref(iss.id)} className="inline-block font-semibold text-act underline">Open finding</Link>
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
          { label: "Replied", count: replied, color: "#2f7a4d" },
          { label: "Waiting for reply", count: Math.max(0, sent - replied), color: "#1d5fb4" },
          { label: "Not sent yet", count: pending, color: "#a86a12" },
          { label: "Escalated", count: escalated, color: "#b3261e" },
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
                  <Link href={mailHref(m.id)} className="font-bold text-act underline decoration-rule underline-offset-2 hover:decoration-current">{m.subject}</Link>
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
export function PlannerReport({ snap, now }: { snap: ObservabilitySnapshot; now: number }) {
  const proposed = snap.decisions.filter((d) => d.status === "proposed").length;
  const approved = snap.decisions.filter((d) => d.status === "approved" || d.status === "executed").length;
  const rejected = snap.decisions.filter((d) => d.status === "rejected").length;
  const outdated = snap.decisions.filter((d) => d.status === "outdated").length;

  return (
    <div className="space-y-6">
      <ForecastPanel forecasts={snap.forecast} now={now} />

      <KpiGrid
        items={[
          { label: "Waiting for approval", value: fmt(proposed), tone: proposed > 0 ? "warn" : "ok" },
          { label: "Approved", value: fmt(approved), tone: "ok" },
          { label: "Rejected", value: fmt(rejected), tone: rejected > 0 ? "stop" : "default" },
          { label: "Outdated", value: fmt(outdated), hint: "Replaced after a newer drop", tone: "default" },
        ]}
      />

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
// 7. Risk Rater Report
// ---------------------------------------------------------------------------
const ratingTiers = [
  { tier: "product", label: "Products", anchor: "risk-products", position: "xl:col-start-1 xl:row-start-1" },
  { tier: "part", label: "Parts", anchor: "risk-parts", position: "xl:col-start-2 xl:row-span-2 xl:row-start-1" },
  { tier: "supplier", label: "Suppliers", anchor: "risk-suppliers", position: "xl:col-start-3 xl:row-span-2 xl:row-start-1" },
] as const;

function Grade({ row }: { row: RiskScore }) {
  const tone = row.grade >= 6 ? "bg-stop-soft text-stop" : row.grade >= 4 ? "bg-warn-soft text-warn" : "bg-ok-soft text-ok";
  return <span className={`inline-block rounded px-1.5 py-0.5 text-xs font-bold tabular ${tone}`}>{row.rating || "Ungraded"}</span>;
}

function ratingLink(row: RiskScore, target: string) {
  const query = new URLSearchParams({ agent: "risk", tier: row.entity_type, entity: row.entity_id });
  return `/observability?${query}#${target}`;
}

export function RiskRaterReport({ snap }: { snap: ObservabilitySnapshot }) {
  const params = useSearchParams();
  const requestedTier = params.get("tier");
  const requestedId = params.get("entity");
  const [names, setNames] = useState<RiskNames>({ product: {}, part: {}, supplier: {} });
  const loadedDrop = snap.health?.lastDrop?.run_id;
  const hasRatings = snap.risk.length > 0;
  useEffect(() => {
    if (!hasRatings) return;
    let active = true;
    // Use customer master data for labels. The rating and its evidence remain readable if a name is missing.
    Promise.all([
      getData<{ product_id: string; name: string }[]>("/api/truth/products").catch(() => []),
      getData<{ component_id: string; description: string }[]>("/api/truth/materials").catch(() => []),
      getData<{ supplier_id: string; name: string }[]>("/api/truth/suppliers").catch(() => []),
    ]).then(([products, materials, suppliers]) => {
      if (active) setNames({
        product: Object.fromEntries(products.map(row => [row.product_id, row.name])),
        part: Object.fromEntries(materials.map(row => [row.component_id, row.description])),
        supplier: Object.fromEntries(suppliers.map(row => [row.supplier_id, row.name])),
      });
    });
    return () => { active = false; };
  }, [hasRatings, loadedDrop]);

  const groups = ratingTiers.map(group => ({
    ...group,
    rows: snap.risk.filter(row => row.entity_type === group.tier).sort((a, b) =>
      b.grade - a.grade || (recordedScore(b) ?? -1) - (recordedScore(a) ?? -1) || a.entity_id.localeCompare(b.entity_id)),
  }));
  const selected = requestedId
    ? snap.risk.find(row => row.entity_type === requestedTier && row.entity_id === requestedId)
    : groups.find(group => group.rows.length)?.rows[0];
  const selectedScore = selected && recordedScore(selected);
  const computedAt = parseTs(selected?.computed_at);
  const selectedBand = riskBands.find(b => b.rating === selected?.rating);
  const selectedGroup = groups.find(g => g.tier === selected?.entity_type);
  const sourceLink = selected?.entity_type === "supplier" && selected.reasons[0]?.signal_inputs;

  return (
    <div className="space-y-6">
      <section id="risk-ratings" aria-labelledby="risk-directory-heading" className="min-w-0 scroll-mt-24">
        <header className="mb-3 flex flex-wrap items-end justify-between gap-x-5 gap-y-2">
          <div>
            <h2 id="risk-directory-heading" className="text-xl font-bold tracking-tight">Current ratings</h2>
            <p className="mt-1 text-sm text-muted">Higher scores mean greater exposure to disruption, on a scale of 0 to 100.</p>
          </div>
          <div aria-label="Risk grade legend" className="text-xs text-muted">
            <p className="mb-1 font-semibold">Minimum score for each grade</p>
            <dl className="flex flex-wrap gap-x-3 gap-y-1 tabular">{riskBands.map(({ rating, minimum, label }) =>
              <div key={rating} className="inline-flex gap-1">
                <dt className="font-semibold text-ink">{rating}<span className="sr-only"> · {label}</span></dt><dd>{minimum}</dd>
              </div>)}</dl>
          </div>
        </header>
        <nav aria-label="Jump to a rating list" className="mb-3 flex flex-wrap gap-x-5 gap-y-2 text-sm font-semibold text-act lg:hidden">
          {groups.map(group => <a key={group.tier} href={`#${group.anchor}`} className="underline underline-offset-2">{group.label} ({group.rows.length})</a>)}
        </nav>
        <div className="grid items-start gap-4 lg:grid-cols-3 xl:grid-rows-[min-content_1fr]">
          {groups.map(({ tier, label, anchor, position, rows }) => <section key={tier} id={anchor} aria-labelledby={anchor + "-heading"}
            className={`@container min-w-0 scroll-mt-24 overflow-hidden rounded-xl border border-rule bg-surface ${position}`}>
            <header className="flex items-baseline justify-between gap-2 border-b border-rule bg-dock/50 px-3 py-2.5 sm:px-4">
              <h3 id={anchor + "-heading"} className="text-base font-bold">{label}</h3>
              <span className="text-xs font-semibold tabular text-muted">{rows.length} rated</span>
            </header>
            {rows.length === 0 ? <p className="px-4 py-4 text-sm text-muted">No {label.toLowerCase()} have a current rating.</p> :
              <ul className="divide-y divide-rule">{rows.map(row => {
                const score = recordedScore(row);
                const active = selected === row;
                return <li key={row.entity_id}>
                  <Link href={ratingLink(row, "risk-rating-detail")} prefetch={false} aria-current={active ? "true" : undefined}
                    className={`block border-l-[3px] px-3 py-2.5 hover:bg-dock/50 focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-act ${active ? "border-l-act bg-act-soft/60" : "border-l-transparent"}`}>
                    <div className="flex flex-col gap-1 @xs:flex-row @xs:items-start @xs:justify-between @xs:gap-2">
                      <span className="min-w-0 text-sm font-semibold leading-snug wrap-anywhere text-ink">{names[tier][row.entity_id] || row.entity_id}</span>
                      <span className="flex shrink-0 items-center gap-1.5">
                        <Grade row={row} />
                        <span className="text-xs tabular text-muted">{score == null ? "No score" : `${score}/100`}</span>
                      </span>
                    </div>
                    <RatingReasonSummary row={row} names={names} />
                  </Link>
                </li>;
              })}</ul>}
          </section>)}
          <section id="risk-rating-detail" aria-labelledby="risk-detail-heading" tabIndex={-1}
            className="min-w-0 scroll-mt-24 rounded-xl border border-rule bg-surface p-4 wrap-anywhere lg:col-span-3 xl:col-span-1 xl:col-start-1 xl:row-start-2">
            {selected ? <>
              <header className="flex flex-wrap items-start justify-between gap-3 border-b border-rule pb-3">
                <div>
                  <p className="text-xs font-semibold capitalize text-muted">{selected.entity_type} · {selected.entity_id}</p>
                  <h2 id="risk-detail-heading" className="mt-1 text-xl font-bold">{names[selected.entity_type][selected.entity_id] || selected.entity_id}</h2>
                  <p className="mt-1 flex flex-wrap items-center gap-2 text-sm text-muted"><Grade row={selected} />
                    {selectedBand && <span>{selectedBand.label} risk</span>}
                    <span>{selectedScore == null ? "Score unavailable" : `${selectedScore}/100`}</span>
                    {computedAt && Number.isFinite(computedAt.getTime()) && <time className="text-xs" dateTime={computedAt.toISOString()}>
                      Updated {computedAt.toLocaleString("en-GB", { dateStyle: "medium", timeStyle: "short", timeZone: "UTC" })} UTC
                    </time>}
                  </p>
                </div>
                {selectedGroup && <a href={`#${selectedGroup.anchor}`} className="text-xs font-semibold text-act underline underline-offset-2">Back to {selectedGroup.label.toLowerCase()}</a>}
              </header>
              <RatingReasons row={selected} names={names} />
              {sourceLink && <a href="#risk-signals" className="mt-3 inline-flex text-sm font-semibold text-act underline underline-offset-2">View this supplier’s market and trade signals</a>}
            </> : <>
              <h2 id="risk-detail-heading" className="text-lg font-bold">Rating details</h2>
              <p className="mt-1 text-sm text-muted">{requestedId ? `No current rating matches ${requestedId}. Choose an entry above to see its evidence.`
                : "Details appear after the Risk Rater publishes a rating."}</p>
            </>}
          </section>
        </div>
      </section>
      <div className="grid items-start gap-4 lg:grid-cols-2">
        <SupplySignals scores={snap.risk} names={names.supplier} preferredSupplier={selected?.entity_type === "supplier" ? selected.entity_id : ""}
          check={snap.health?.lastRiskCheck} trace={snap.trace} version={snap.health?.activity_version} />
      </div>
      <RiskPlaybook activityVersion={snap.health?.activity_version} />
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
