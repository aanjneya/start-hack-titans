"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import {
  eur,
  fmt,
  getData,
  postData,
  type PreemptiveChaseAlert,
  type ProductUpgradePlan,
  type RiskPlaybookResponse,
  type SecondSourceScout,
} from "@/lib/api";

function gradeBadgeClass(grade: number): string {
  if (grade >= 6) return "bg-stop text-white";
  if (grade === 5) return "bg-stop-soft text-stop";
  if (grade === 4) return "bg-warn-soft text-warn";
  if (grade === 3) return "bg-act-soft text-act";
  return "bg-ok-soft text-ok";
}

function RiskScenarios({ plans, onReviewAction }: {
  plans: ProductUpgradePlan[];
  onReviewAction?: (key: string) => void;
}) {
  const [selectedId, setSelectedId] = useState("");
  const [stepCount, setStepCount] = useState(1);
  const plan = plans.find(item => item.product_id === selectedId) ?? plans[0];
  if (!plan) return <p className="text-sm text-muted">A scenario will appear when products and their component risks have been rated.</p>;

  const count = Math.min(stepCount, plan.levers.length);
  const steps = plan.levers.slice(0, count);
  const result = steps.at(-1);
  const projected = result?.cumulative_score ?? plan.current_score;
  const difference = Number((plan.current_score - projected).toFixed(1));

  return (
    <>
      <div className="mt-4 grid gap-3 sm:grid-cols-2">
        <div className="flex min-w-0 flex-col gap-1">
          <label htmlFor="scenario-product" className="text-xs font-semibold text-muted">Product</label>
          <select id="scenario-product" className="min-w-0 rounded-md border border-rule bg-surface px-2 py-2 text-sm text-ink" value={plan.product_id}
            onChange={e => { setSelectedId(e.target.value); setStepCount(1); }}>
            {plans.map(p => <option key={p.product_id} value={p.product_id}>{p.product_name}</option>)}
          </select>
        </div>
        {plan.levers.length > 0 && <div className="flex min-w-0 flex-col gap-1">
          <label htmlFor="scenario-changes" className="text-xs font-semibold text-muted">Changes to model</label>
          <select id="scenario-changes" className="min-w-0 rounded-md border border-rule bg-surface px-2 py-2 text-sm text-ink" value={count}
            onChange={e => setStepCount(Number(e.target.value))}>
            <option value={0}>Current conditions</option>
            {plan.levers.map((step, index) => <option key={step.id} value={index + 1}>
              {index + 1}. {index > 0 ? "Also " + step.title.charAt(0).toLowerCase() + step.title.slice(1) : step.title}
            </option>)}
          </select>
        </div>}
      </div>
      <div aria-live="polite" className="mt-4 rounded-md border border-rule bg-dock/45 p-4">
        <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
          <span className="text-sm text-muted">Current risk</span>
          <strong className="tabular text-base">{plan.current_score}/100 <span className="text-sm">{plan.current_rating}</span></strong>
          {result && difference !== 0 && <>
            <span className="text-sm text-muted">Estimated after {count === 1 ? "this change" : "these changes"}</span>
            <strong className="tabular text-base text-act">{projected}/100 <span className="text-sm">{result.cumulative_rating}</span></strong>
          </>}
        </div>
        <p className="mt-1 text-sm text-ink">
          {!result ? (plan.levers.length ? "Select a change to see its estimated effect." : "No change to model from the current evidence.")
            : difference === 0 ? "These conditions would leave the product’s risk score unchanged."
            : `${difference} points lower.${plan.current_rating === result.cumulative_rating ? ` The rating stays ${plan.current_rating}.` : ""}`}
        </p>
        {result?.limiting_part_after && <p className="mt-1 text-xs text-muted">{result.limiting_part_after} still sets the highest part risk.</p>}
      </div>
      <ol className="mt-2 divide-y divide-rule">
        {steps.map((step, index) => (
          <li key={step.id} className="py-3">
            <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
              <span className="text-sm font-semibold text-ink">{count > 1 && `${index + 1}. `}{step.title}</span>
              <span className="text-xs font-medium text-muted">{step.delta_points > 0 ? `${step.delta_points} fewer risk points` : "No change to the product score"}</span>
            </div>
            <p className="mt-1 text-xs text-muted">{step.rationale}</p>
            {step.queue_key && <div className="mt-2 space-y-1 border-l-2 border-act/40 pl-3 text-xs">
              {onReviewAction
                ? <button className="text-left font-semibold text-act underline" onClick={() => onReviewAction(step.queue_key!)}>Review buyer action · {step.decision_label}</button>
                : <Link href={`/?action=${encodeURIComponent(step.queue_key)}`} className="font-semibold text-act underline">Review buyer action · {step.decision_label}</Link>}
              <p className="text-muted">{step.cost_eur == null ? "Buyer action cost awaiting confirmation" : `Buyer action amount ${eur(step.cost_eur)}`}
                {step.approver && ` · Assigned to ${step.approver}`}
                {step.copies_gained != null && step.copies_gained > 0 && ` · Up to ${fmt(step.copies_gained)} additional copies if confirmed`}</p>
            </div>}
          </li>
        ))}
      </ol>
    </>
  );
}

export default function RiskPlaybook({
  onActionComplete,
  onReviewAction,
  activityVersion,
}: {
  onActionComplete?: () => Promise<void> | void;
  onReviewAction?: (key: string) => void;
  activityVersion?: string | null;
}) {
  const [playbook, setPlaybook] = useState<RiskPlaybookResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [busyId, setBusyId] = useState<string | null>(null);
  const [toast, setToast] = useState("");
  const [openScout, setOpenScout] = useState<string | null>(null);
  const [openDraft, setOpenDraft] = useState<string | null>(null);
  const [copiedId, setCopiedId] = useState<string | null>(null);

  const applyPlaybook = useCallback((data: RiskPlaybookResponse) => {
    setPlaybook(data);
    setError("");
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
  }, [applyPlaybook, activityVersion]);

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
      setToast(`Supplier check requested for ${alert.supplier_name} (${alert.language}).`);
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
      <div id="risk-scenarios" className="mb-6 rounded-xl border border-rule bg-surface p-5 text-sm text-muted">
        Loading risk scenarios…
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

      <section id="risk-scenarios" className="scroll-mt-6 rounded-xl border border-rule bg-surface p-5">
        <h3 className="text-base font-bold text-ink">What would lower product risk?</h3>
        <p className="mt-1 text-sm text-muted">Compare today’s rating with the estimate if these changes are completed. Evidence of the change is what updates the actual rating.</p>
        <RiskScenarios plans={playbook.upgrade_plans} onReviewAction={onReviewAction} />
      </section>

      {playbook.circuit_breakers.length > 0 && (
        <section className="rounded-xl border border-rule bg-surface p-5">
          <div className="flex flex-wrap items-center justify-between gap-2 border-b border-rule pb-3">
            <div>
              <h3 className="text-base font-bold text-ink">Suggested safeguards</h3>
              <p className="text-xs text-muted">
                Controls to review under company policy when a supplier or product reaches B or CCC.
              </p>
            </div>
            <span className="rounded-full bg-stop-soft px-2.5 py-1 text-xs font-bold text-stop tabular">
              {playbook.circuit_breakers.length} flagged
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
                <details className="mt-2 text-xs">
                  <summary className="cursor-pointer font-semibold text-act">Review {cb.rules_triggered.length} suggested controls</summary>
                  <p className="mt-2 text-muted">{cb.reason}</p>
                  <div className="mt-2 flex flex-wrap gap-1.5">
                    {cb.rules_triggered.map((rule) => (
                      <span key={rule} className="rounded-md border border-stop/25 bg-surface px-2 py-0.5 text-[11px] font-semibold text-stop">
                        {rule}
                      </span>
                    ))}
                  </div>
                </details>
              </div>
            ))}
          </div>
        </section>
      )}

      {playbook.second_source_scouts.length > 0 && (
        <section className="rounded-xl border border-rule bg-surface p-5">
          <div className="flex flex-wrap items-center justify-between gap-2 border-b border-rule pb-3">
            <div>
              <h3 className="text-base font-bold text-ink">Backup suppliers</h3>
              <p className="text-xs text-muted">
                The estimate assumes a second supplier starts delivering and supplier health stays the same. Verify the example suppliers, prices and lead times before requesting a quote.
              </p>
            </div>
            <span className="rounded-full bg-act-soft px-2.5 py-1 text-xs font-bold text-act tabular">
              {playbook.second_source_scouts.length} single-source parts
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
                          Outside {sc.blocked_country}
                        </span>
                      </div>
                      <p className="mt-0.5 text-xs text-muted">
                        Sole supplier: {sc.current_supplier_name} ({sc.current_supplier_id}) · Alternative countries:{" "}
                        {sc.safe_corridors.map((c) => `${c.country} (${c.rating})`).join(", ")}
                      </p>
                    </div>
                    <div className="flex items-center gap-2">
                      <span className={`rounded px-2 py-0.5 text-xs font-bold ${gradeBadgeClass(sc.current_grade)}`}>
                        {sc.current_rating}
                      </span>
                      <span className="text-xs text-muted">could reach</span>
                      <span className={`rounded px-2 py-0.5 text-xs font-bold ${gradeBadgeClass(sc.projected_dual_grade)}`}>
                        {sc.projected_dual_rating} with two sources
                      </span>
                      <button
                        onClick={() => setOpenScout(expanded ? null : sc.part_id)}
                        className="rounded border border-act/40 bg-act-soft px-2.5 py-1 text-xs font-semibold text-act hover:bg-act hover:text-white"
                      >
                        {expanded ? "Hide candidates" : "See candidates"}
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
                          <span className="font-bold text-ink">Request for quote · {sc.rfq_subject}</span>
                          <button
                            onClick={() => void copyRfq(sc)}
                            className="rounded bg-ink px-2.5 py-1 text-[11px] font-semibold text-white hover:bg-act"
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

      {playbook.preemptive_alerts.length > 0 && (
        <section className="rounded-xl border border-rule bg-surface p-5">
          <div className="flex flex-wrap items-center justify-between gap-2 border-b border-rule pb-3">
            <div>
              <h3 className="text-base font-bold text-ink">Early supplier checks</h3>
              <p className="text-xs text-muted">
                The Chaser can ask suppliers about a warning before a delay appears in the order record.
              </p>
            </div>
            <span className="rounded-full bg-warn-soft px-2.5 py-1 text-xs font-bold text-warn tabular">
              {playbook.preemptive_alerts.length} alerts
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
                          {al.lead_time_advantage_hours}h planning window
                        </span>
                        <span className="text-xs font-bold text-ink">{al.supplier_name} ({al.country})</span>
                        <span className="text-xs text-muted">· {al.provider}</span>
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
                        <span className="rounded bg-ok-soft px-2.5 py-1 text-xs font-bold text-ok">Requested</span>
                      ) : al.status === "blocked_anti_spam" ? (
                        <span className="rounded bg-dock px-2.5 py-1 text-xs font-semibold text-muted" title="Chaser already has an open thread with this supplier">
                          Existing thread open
                        </span>
                      ) : (
                        <button
                          onClick={() => void dispatchPreemptive(al)}
                          disabled={busyId === al.alert_id}
                          className="rounded bg-act px-2.5 py-1 text-xs font-semibold text-white hover:bg-ink disabled:opacity-50"
                        >
                          {busyId === al.alert_id ? "Sending…" : "Request update"}
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
                        <p className="font-semibold text-muted">English version</p>
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

    </div>
  );
}
