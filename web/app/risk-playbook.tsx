"use client";

import { useCallback, useEffect, useState } from "react";
import {
  eur,
  fmt,
  getData,
  postData,
  type PreemptiveChaseAlert,
  type ProductUpgradePlan,
  type RiskPlaybookResponse,
  type RiskUpgradeLever,
  type SecondSourceScout,
} from "@/lib/api";

function scoreToGrade(score: number): { grade: number; rating: string } {
  const s = Math.max(0, Math.min(100, score));
  if (s >= 80) return { grade: 6, rating: "CCC" };
  if (s >= 65) return { grade: 5, rating: "B" };
  if (s >= 50) return { grade: 4, rating: "BBB" };
  if (s >= 35) return { grade: 3, rating: "A" };
  if (s >= 20) return { grade: 2, rating: "AA" };
  return { grade: 1, rating: "AAA" };
}

// Risk colours for this panel only: red while the risk is high, amber in between, green once it is low.
const RISK_RED = "#c83e3a";
const riskColor = (grade: number) => (grade >= 5 ? RISK_RED : grade === 4 ? "var(--color-warn)" : "var(--color-ok)");
function gradeBadgeClass(grade: number): string {
  if (grade >= 6) return "bg-[#c83e3a] text-white";
  if (grade === 5) return "bg-[#fbeceb] text-[#a8322f]";
  if (grade === 4) return "bg-warn-soft text-warn";
  if (grade === 3) return "bg-act-soft text-act";
  return "bg-ok-soft text-ok";
}

export default function RiskPlaybook({
  onActionComplete,
  signer = "Paul Winter",
  mock = true,
  refreshKey,
}: {
  onActionComplete?: () => Promise<void> | void;
  signer?: string;
  /** Changes when a new risk check finishes (e.g. its run id): the playbook reloads so it never shows the previous drop. */
  refreshKey?: string;
  /** Demo mode: the outside feeds are offline snapshots, so never call them "live". */
  mock?: boolean;
}) {
  const [playbook, setPlaybook] = useState<RiskPlaybookResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [busyId, setBusyId] = useState<string | null>(null);
  const [toast, setToast] = useState("");
  const [selectedLevers, setSelectedLevers] = useState<Record<string, Record<string, boolean>>>({});
  const [openScout, setOpenScout] = useState<string | null>(null);
  const [openDraft, setOpenDraft] = useState<string | null>(null);
  const [copiedId, setCopiedId] = useState<string | null>(null);

  const applyPlaybook = useCallback((data: RiskPlaybookResponse) => {
    setPlaybook(data);
    setError("");
    setSelectedLevers((prev) => {
      const next = { ...prev };
      for (const plan of data.upgrade_plans) {
        if (!next[plan.product_id]) {
          const init: Record<string, boolean> = {};
          for (const lv of plan.levers) init[lv.id] = true;
          next[plan.product_id] = init;
        }
      }
      return next;
    });
  }, []);

  const load = useCallback(async () => {
    try {
      const data = await getData<RiskPlaybookResponse>("/api/risk/playbook");
      applyPlaybook(data);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not load Risk Playbook");
    } finally {
      setLoading(false);
    }
  }, [applyPlaybook]);

  useEffect(() => {
    let active = true;
    getData<RiskPlaybookResponse>("/api/risk/playbook")
      .then((data) => {
        if (active) applyPlaybook(data);
      })
      .catch((e) => {
        if (active) setError(e instanceof Error ? e.message : "Could not load Risk Playbook");
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [applyPlaybook, refreshKey]);

  const toggleLever = (productId: string, leverId: string) => {
    setSelectedLevers((prev) => ({
      ...prev,
      [productId]: {
        ...(prev[productId] ?? {}),
        [leverId]: !(prev[productId]?.[leverId] ?? false),
      },
    }));
  };

  const executeUpgradePlan = async (plan: ProductUpgradePlan) => {
    const sel = selectedLevers[plan.product_id] ?? {};
    const execLevers = plan.levers.filter((lv) => sel[lv.id] && lv.executable && lv.decision_id != null);
    if (execLevers.length === 0) {
      setToast("Selected structural levers staged for procurement — approve linked decision cards on the main inbox.");
      return;
    }
    setBusyId(`exec-${plan.product_id}`);
    setToast("");
    let okCount = 0;
    const errors: string[] = [];
    for (const lv of execLevers) {
      try {
        await postData(`/api/decisions/${lv.decision_id}/approve`, {
          by: signer,
          option: lv.option_index ?? undefined,
        });
        okCount += 1;
      } catch (e) {
        errors.push(e instanceof Error ? e.message : `Decision #${lv.decision_id} failed`);
      }
    }
    setBusyId(null);
    if (okCount > 0) {
      setToast(`Executed ${okCount} upgrade decision(s) for ${plan.product_name} — forecast & risk refreshed.`);
      await load();
      await onActionComplete?.();
    } else if (errors.length > 0) {
      setToast(errors[0]);
    }
  };

  const dispatchPreemptive = async (alert: PreemptiveChaseAlert) => {
    setBusyId(alert.alert_id);
    setToast("");
    try {
      await postData("/api/risk/preemptive-chase", {
        alert_id: alert.alert_id,
        supplier_id: alert.supplier_id,
        shipment_id: alert.shipment_id,
        po_number: alert.po_number,
        part_id: alert.part_id,
        ask: alert.draft_body_en,
      });
      setToast(`Pre-emptive corridor check dispatched to ${alert.supplier_name} (${alert.language}).`);
      await load();
      await onActionComplete?.();
    } catch (e) {
      setToast(e instanceof Error ? e.message : "Pre-emptive dispatch failed");
    } finally {
      setBusyId(null);
    }
  };

  const copyRfq = async (scout: SecondSourceScout) => {
    try {
      await navigator.clipboard.writeText(`Subject: ${scout.rfq_subject}\n\n${scout.rfq_body}`);
      setCopiedId(scout.part_id);
      setTimeout(() => setCopiedId(null), 2000);
    } catch {
      setToast("Copy blocked by browser — select text from the RFQ box.");
    }
  };

  if (loading && !playbook) {
    return (
      <div className="mb-6 rounded-xl border border-rule bg-surface p-5 text-sm text-muted">
        Computing Risk-to-Action Playbook (Path-to-Upgrade · Pre-Emptive Chasing · 2nd-Source Scout · Circuit Breakers)…
      </div>
    );
  }
  if (error || !playbook) {
    return error ? (
      <div className="mb-6 rounded-xl border border-rule bg-warn-soft p-4 text-sm text-warn">{error}</div>
    ) : null;
  }

  return (
    <div className="mb-6 space-y-5">
      {toast && (
        <div role="status" className="flex items-center justify-between rounded-lg border border-ok/30 bg-ok-soft px-4 py-2.5 text-sm font-medium text-ok">
          <span>{toast}</span>
          <button onClick={() => setToast("")} className="ml-4 text-xs underline">
            Dismiss
          </button>
        </div>
      )}

      {/* 1. Path to a better rating (CCC -> A) */}
      {playbook.upgrade_plans.length > 0 && (
        <section className="rounded-xl border border-rule bg-surface p-5">
          <div className="flex flex-wrap items-center justify-between gap-2 border-b border-rule pb-3">
            <div>
              <h3 className="text-base font-bold text-ink">Path to a better rating</h3>
              <p className="text-xs text-muted">
                Pick actions to see how far the risk drops. Ranked by risk removed per euro.
              </p>
            </div>
            <span className="rounded-full bg-dock px-2.5 py-1 text-xs font-semibold text-muted">
              Mode: {playbook.autonomy_mode === "auto" ? "Autonomous" : "Human Control Gate"}
            </span>
          </div>

          <div className="mt-4 grid gap-4 lg:grid-cols-2">
            {playbook.upgrade_plans.map((plan) => {
              const sel = selectedLevers[plan.product_id] ?? {};
              const activeLevers = plan.levers.filter((lv) => sel[lv.id]);
              const totalDelta = activeLevers.reduce((acc, lv) => acc + lv.delta_points, 0);
              const simScore = Math.max(12, Math.round((plan.current_score - totalDelta) * 10) / 10);
              const sim = scoreToGrade(simScore);
              const simCost = activeLevers.reduce((acc, lv) => acc + lv.cost_eur, 0);
              const execCount = activeLevers.filter((lv) => lv.executable && lv.decision_id != null).length;

              return (
                <div key={plan.product_id} className="flex flex-col rounded-lg border border-rule bg-dock/35 p-4">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <div>
                      <span className="text-xs font-mono text-muted">{plan.product_id}</span>
                      <h4 className="text-base font-bold text-ink">{plan.product_name}</h4>
                    </div>
                    <div className="flex items-center gap-2">
                      <span className={`rounded px-2 py-0.5 text-xs font-bold tabular ${gradeBadgeClass(plan.current_grade)}`}>
                        {plan.current_rating} ({Math.round(plan.current_score)})
                      </span>
                      <span className="text-xs font-bold text-muted">→</span>
                      <span className={`rounded px-2.5 py-0.5 text-sm font-bold tabular transition-all ${gradeBadgeClass(sim.grade)}`}>
                        {sim.rating} ({Math.round(simScore)})
                      </span>
                    </div>
                  </div>

                  {/* Live Score Bar */}
                  <div className="mt-3">
                    <div className="flex justify-between text-[11px] text-muted tabular">
                      <span>Risk {Math.round(plan.current_score)} → {Math.round(simScore)} / 100 · lower is better</span>
                      <span className="font-semibold text-ok">
                        −{Math.round(plan.current_score - simScore)} pts · {eur(simCost)}
                      </span>
                    </div>
                    {/* Faint red: the risk today. Solid: the risk after the picked actions, red to green as it drops. */}
                    <div className="relative mt-1 h-2.5 w-full overflow-hidden rounded-full bg-dock">
                      <div className="absolute inset-y-0 left-0 rounded-full" style={{ width: `${Math.min(100, Math.max(6, plan.current_score))}%`, background: RISK_RED, opacity: 0.18 }} />
                      <div
                        className="absolute inset-y-0 left-0 rounded-full transition-all duration-300"
                        style={{ width: `${Math.min(100, Math.max(6, simScore))}%`, background: riskColor(sim.grade) }}
                      />
                    </div>
                  </div>

                  {/* Interactive Levers */}
                  <ul className="mt-3 flex-1 space-y-2">
                    {plan.levers.map((lv: RiskUpgradeLever) => {
                      const checked = !!sel[lv.id];
                      return (
                        <li key={lv.id}>
                          <label className={`flex cursor-pointer items-start gap-2.5 rounded-md border p-2.5 text-xs transition ${checked ? "border-act/40 bg-surface" : "border-rule/70 bg-dock/40 opacity-70"}`}>
                            <input
                              type="checkbox"
                              checked={checked}
                              onChange={() => toggleLever(plan.product_id, lv.id)}
                              className="mt-0.5 h-3.5 w-3.5 accent-act"
                            />
                            <div className="min-w-0 flex-1">
                              <div className="flex flex-wrap items-center justify-between gap-1">
                                <span className="font-semibold text-ink">{lv.title}</span>
                                <span className="rounded bg-ok-soft px-1.5 py-0.5 text-[11px] font-bold text-ok tabular">
                                  −{Math.round(lv.delta_points)} pts
                                </span>
                              </div>
                              <p className="mt-0.5 text-[11px] text-muted">{lv.rationale}</p>
                              <div className="mt-1 flex flex-wrap gap-2 text-[11px] text-muted tabular">
                                <span className="font-semibold text-ink">{eur(lv.cost_eur)}</span>
                                {lv.copies_gained != null && <span>· +{fmt(lv.copies_gained)} copies</span>}
                                <span>· Approver: {lv.approver}</span>
                                {lv.executable && (
                                  <span className="rounded bg-act-soft px-1.5 py-0.5 text-[10px] font-semibold text-act">
                                    Live Decision #{lv.decision_id}
                                  </span>
                                )}
                              </div>
                            </div>
                          </label>
                        </li>
                      );
                    })}
                  </ul>

                  <div className="mt-3 flex items-center justify-between border-t border-rule pt-3">
                    <span className="text-xs text-muted tabular">
                      {activeLevers.length}/{plan.levers.length} levers · Total {eur(simCost)}
                    </span>
                    <button
                      onClick={() => void executeUpgradePlan(plan)}
                      disabled={busyId === `exec-${plan.product_id}` || activeLevers.length === 0}
                      className="rounded-md bg-act px-3 py-1.5 text-xs font-semibold text-white transition hover:bg-brand-ink disabled:opacity-50"
                    >
                      {busyId === `exec-${plan.product_id}`
                        ? "Executing…"
                        : execCount > 0
                          ? `Execute Upgrade (${execCount} live)`
                          : "Stage Upgrade Plan"}
                    </button>
                  </div>
                </div>
              );
            })}
          </div>
        </section>
      )}

      {/* 2. RISK-TRIGGERED CIRCUIT BREAKERS (AUTO-POLICY SHIFT) */}
      {playbook.circuit_breakers.length > 0 && (
        <section className="rounded-xl border border-rule bg-surface p-5">
          <div className="flex flex-wrap items-center justify-between gap-2 border-b border-rule pb-3">
            <div>
              <h3 className="text-base font-bold text-ink">Risk-Triggered Circuit Breakers</h3>
              <p className="text-xs text-muted">
                Automatic governance safeguards driven by customer setup JSON when suppliers or products hit Grade B / CCC.
              </p>
            </div>
            <span className="rounded-full bg-stop-soft px-2.5 py-1 text-xs font-bold text-stop tabular">
              {playbook.circuit_breakers.length} Active
            </span>
          </div>
          <div className="mt-3 grid gap-3 md:grid-cols-2">
            {playbook.circuit_breakers.map((cb) => (
              <div key={`${cb.entity_type}-${cb.entity_id}`} className="rounded-lg border border-stop/25 bg-stop-soft/35 p-3.5">
                <div className="flex items-center justify-between gap-2">
                  <div>
                    <span className="text-[11px] font-semibold uppercase tracking-wide text-muted">{cb.entity_type}</span>
                    <h4 className="text-sm font-bold text-ink">
                      {cb.entity_name} <span className="font-mono text-xs text-muted">({cb.entity_id})</span>
                    </h4>
                  </div>
                  <span className={`rounded px-2 py-0.5 text-xs font-bold ${gradeBadgeClass(cb.grade)}`}>
                    {cb.rating} ({Math.round(cb.score_0_100)})
                  </span>
                </div>
                <p className="mt-1 text-xs text-muted">{cb.reason}</p>
                <div className="mt-2 flex flex-wrap gap-1.5">
                  {cb.rules_triggered.map((rule) => (
                    <span key={rule} className="rounded-md border border-stop/25 bg-surface px-2 py-0.5 text-[11px] font-semibold text-stop">
                      {rule}
                    </span>
                  ))}
                </div>
              </div>
            ))}
          </div>
        </section>
      )}

      {/* 3. PREDICTIVE PRE-EMPTIVE CHASING (72H BEFORE SAP KNOWS) */}
      {playbook.preemptive_alerts.length > 0 && (
        <section className="rounded-xl border border-rule bg-surface p-5">
          <div className="flex flex-wrap items-center justify-between gap-2 border-b border-rule pb-3">
            <div>
              <h3 className="text-base font-bold text-ink">Early warnings from outside feeds → Chaser</h3>
              <p className="text-xs text-muted">
                Polymarket, weather and regulation signals matched to active shipping routes 24–72 h before the ERP shows a delay.
              </p>
            </div>
            <span className="rounded-full bg-warn-soft px-2.5 py-1 text-xs font-bold text-warn tabular">
              {playbook.preemptive_alerts.length} Corridor Alerts
            </span>
          </div>
          <div className="mt-3 space-y-2.5">
            {playbook.preemptive_alerts.map((al) => {
              const expanded = openDraft === al.alert_id;
              return (
                <div key={al.alert_id} className="rounded-lg border border-rule bg-dock/30 p-3.5">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="rounded bg-warn-soft px-2 py-0.5 text-[11px] font-bold text-warn">
                          +{al.lead_time_advantage_hours}h ahead of ERP
                        </span>
                        <span className="text-xs font-bold text-ink">{al.supplier_name} ({al.country})</span>
                        <span className="text-xs text-muted">· {mock ? al.provider.replace(/\s*Live Odds$/i, " (offline snapshot)") : al.provider}</span>
                        {al.shipment_id && <span className="font-mono text-[11px] text-muted">{al.shipment_id}</span>}
                      </div>
                      <p className="mt-1 text-xs text-ink">{al.signal_title}</p>
                    </div>
                    <div className="flex items-center gap-2">
                      <button
                        onClick={() => setOpenDraft(expanded ? null : al.alert_id)}
                        className="rounded border border-rule bg-surface px-2.5 py-1 text-xs font-semibold text-ink hover:bg-dock"
                      >
                        {expanded ? "Hide Draft" : `Preview (${al.language})`}
                      </button>
                      {al.status === "dispatched" ? (
                        <span className="rounded bg-ok-soft px-2.5 py-1 text-xs font-bold text-ok">Dispatched ✓</span>
                      ) : al.status === "blocked_anti_spam" ? (
                        <span className="rounded bg-dock px-2.5 py-1 text-xs font-semibold text-muted" title="Chaser already has an open thread with this supplier">
                          Anti-Spam Hold
                        </span>
                      ) : (
                        <button
                          onClick={() => void dispatchPreemptive(al)}
                          disabled={busyId === al.alert_id}
                          className="rounded bg-act px-2.5 py-1 text-xs font-semibold text-white hover:bg-brand-ink disabled:opacity-50"
                        >
                          {busyId === al.alert_id ? "Sending…" : "Dispatch Check"}
                        </button>
                      )}
                    </div>
                  </div>
                  {expanded && (
                    <div className="mt-3 grid gap-2 border-t border-rule pt-2.5 md:grid-cols-2">
                      <div className="rounded bg-surface p-2.5 text-xs">
                        <p className="font-semibold text-muted">Localized ({al.language}) · {al.draft_subject}</p>
                        <pre className="mt-1 whitespace-pre-wrap font-sans text-ink">{al.draft_body}</pre>
                      </div>
                      <div className="rounded bg-surface p-2.5 text-xs">
                        <p className="font-semibold text-muted">English Translation</p>
                        <pre className="mt-1 whitespace-pre-wrap font-sans text-ink">{al.draft_body_en}</pre>
                      </div>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </section>
      )}

      {/* 4. AUTONOMOUS GEO-DECORRELATED 2ND-SOURCE SCOUT + SPEC-GROUNDED RFQ */}
      {playbook.second_source_scouts.length > 0 && (
        <section className="rounded-xl border border-rule bg-surface p-5">
          <div className="flex flex-wrap items-center justify-between gap-2 border-b border-rule pb-3">
            <div>
              <h3 className="text-base font-bold text-ink">Geo-Decorrelated 2nd-Source Scout &amp; Instant RFQ Pack</h3>
              <p className="text-xs text-muted">
                Excludes embargoed/high-risk origins using route feeds, simulates 70/30 dual-source grade recovery, and drafts spec-grounded RFQs.
              </p>
            </div>
            <span className="rounded-full bg-act-soft px-2.5 py-1 text-xs font-bold text-act tabular">
              {playbook.second_source_scouts.length} Single-Source Parts
            </span>
          </div>
          <div className="mt-3 space-y-2.5">
            {playbook.second_source_scouts.map((sc) => {
              const expanded = openScout === sc.part_id;
              return (
                <div key={sc.part_id} className="rounded-lg border border-rule bg-dock/30 p-3.5">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <div>
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="font-mono text-xs font-bold text-ink">{sc.part_id}</span>
                        <span className="text-sm font-bold text-ink">{sc.part_name}</span>
                        <span className="rounded bg-stop-soft px-1.5 py-0.5 text-[11px] font-semibold text-stop">
                          Origin {sc.blocked_country} Excluded
                        </span>
                      </div>
                      <p className="mt-0.5 text-xs text-muted">
                        Sole supplier: {sc.current_supplier_name} ({sc.current_supplier_id}) · Safe corridors:{" "}
                        {sc.safe_corridors.map((c) => `${c.country} (${c.rating})`).join(", ")}
                      </p>
                    </div>
                    <div className="flex items-center gap-2">
                      <span className={`rounded px-2 py-0.5 text-xs font-bold ${gradeBadgeClass(sc.current_grade)}`}>
                        {sc.current_rating}
                      </span>
                      <span className="text-xs text-muted">→</span>
                      <span className={`rounded px-2 py-0.5 text-xs font-bold ${gradeBadgeClass(sc.projected_dual_grade)}`}>
                        {sc.projected_dual_rating} (70/30)
                      </span>
                      <button
                        onClick={() => setOpenScout(expanded ? null : sc.part_id)}
                        className="rounded border border-act/40 bg-act-soft px-2.5 py-1 text-xs font-semibold text-act hover:bg-act hover:text-white"
                      >
                        {expanded ? "Close Scout" : "Scout 2nd Sources →"}
                      </button>
                    </div>
                  </div>

                  {expanded && (
                    <div className="mt-3 space-y-3 border-t border-rule pt-3">
                      <div className="grid gap-2.5 md:grid-cols-3">
                        {sc.candidates.map((cand) => (
                          <div key={cand.name} className="rounded-md border border-rule bg-surface p-3 text-xs">
                            <div className="flex items-start justify-between gap-1">
                              <span className="font-bold text-ink">{cand.name}</span>
                              <span className="rounded bg-ok-soft px-1.5 py-0.5 text-[11px] font-bold text-ok">
                                {cand.country} · {cand.corridor_rating}
                              </span>
                            </div>
                            <p className="mt-0.5 text-[11px] text-muted">
                              {cand.city} · Lead {cand.lead_time_days}d · Qual {cand.qualification_weeks}w
                              {cand.est_unit_price_eur != null ? ` · ~€${cand.est_unit_price_eur.toFixed(2)}/u` : ""}
                            </p>
                            <p className="mt-1.5 text-[11px] text-ink">{cand.why_decorrelated}</p>
                            <div className="mt-2 flex flex-wrap gap-1">
                              {cand.certifications.map((cert) => (
                                <span key={cert} className="rounded bg-dock px-1.5 py-0.5 text-[10px] font-medium text-muted">
                                  {cert}
                                </span>
                              ))}
                            </div>
                          </div>
                        ))}
                      </div>
                      <div className="rounded-md border border-rule bg-surface p-3 text-xs">
                        <div className="flex items-center justify-between gap-2">
                          <span className="font-bold text-ink">Spec-Grounded RFQ Draft · {sc.rfq_subject}</span>
                          <button
                            onClick={() => void copyRfq(sc)}
                            className="rounded bg-primary px-2.5 py-1 text-[11px] font-semibold text-primary-foreground hover:bg-brand-ink"
                          >
                            {copiedId === sc.part_id ? "Copied ✓" : "Copy RFQ"}
                          </button>
                        </div>
                        <pre className="mt-2 whitespace-pre-wrap font-sans text-[11px] text-muted">{sc.rfq_body}</pre>
                      </div>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </section>
      )}
    </div>
  );
}
