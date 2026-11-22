import Link from "next/link";
import { useEffect, useState } from "react";
import { actionHref } from "./buyer-links";
import { fmt, getData, type Forecast, type OutboxMessage, type RiskScore, type ExtendedRiskReason } from "@/lib/api";
import { recordedScore, riskBands } from "./observability/risk-explanations";
import type { BuyerActivity, BuyerQueueEntry } from "../../API_CONTRACT";

type Delivery = "sent" | "simulated" | "unverified";

/** Keep the original delivery evidence even if the deployment's current mode changes later. */
export function emailDeliveries(messages: OutboxMessage[], trace: BuyerActivity[], replay: boolean) {
  const recorded = new Map<number, Delivery>();
  for (const step of trace) {
    if (step.agent !== "chaser" || typeof step.data.outbox_id !== "number") continue;
    if (step.action === "send_email") recorded.set(step.data.outbox_id, "sent");
    if (step.action === "mock_send") recorded.set(step.data.outbox_id, "simulated");
  }
  const deliveries = new Map<number, Delivery>();
  for (const mail of messages) {
    if (mail.sent_at || mail.status === "sent" || mail.status === "replied") {
      const delivery = mail.sent_copy_pending != null ? "sent" : recorded.get(mail.id);
      deliveries.set(mail.id, delivery ?? (replay && mail.sent_at ? "simulated" : "unverified"));
    }
  }
  return deliveries;
}

const linkStyle = "shrink-0 text-xs font-semibold text-act hover:underline";

type Signal = NonNullable<RiskScore["reasons"][number]["signal_inputs"]>[string][number];

function sourceLabel(signal: Signal) {
  if (signal.live === false) return "Offline example";
  if (signal.provider === "portal_news") return "Customer news record";
  if (signal.provider === "telemetry") return "Shipment tracking";
  if (signal.provider === "polymarket") return "Polymarket";
  if (signal.provider === "gnews") return "News coverage";
  return "Recorded signal";
}

/** Mounted only when the linked decisions have no supplier name. Health polling does not repeat this read. */
function RegisterName({ supplierId }: { supplierId: string }) {
  const [name, setName] = useState("");
  useEffect(() => {
    let active = true;
    getData<{supplier_id: string; name: string; legal_name?: string}[]>(
      `/api/truth/suppliers?q=${encodeURIComponent(supplierId)}`
    ).then(rows => {
      const match = rows.find(row => row.supplier_id === supplierId);
      if (active && match) setName(match.name?.trim() || match.legal_name?.trim() || "");
    }).catch(() => { /* The recorded supplier ID remains available. */ });
    return () => { active = false; };
  }, [supplierId]);
  return <>{name || supplierId}</>;
}

export function SupplierRiskWatch({ scores, queue, updated, waiting }: {
  scores: RiskScore[]; queue: BuyerQueueEntry[] | null; updated?: BuyerQueueEntry; waiting: boolean;
}) {
  const changedId = updated?.option.actions.find(action => action.supplier_id)?.supplier_id;
  const supplier = scores.find(row => row.entity_type === "supplier" && row.entity_id === changedId)
    ?? scores.filter(row => row.entity_type === "supplier").sort((a,b) =>
    b.grade - a.grade || (recordedScore(b) ?? -1) - (recordedScore(a) ?? -1) || a.entity_id.localeCompare(b.entity_id))[0];
  if (!supplier) return null;

  const name = queue?.flatMap(entry => entry.option.exposure?.rating_path ?? [])
    .find(node => node.tier === "supplier" && node.id === supplier.entity_id && node.name?.trim() && node.name !== node.id)?.name;
  const score = recordedScore(supplier);
  const band = riskBands.find(b => b.rating === supplier.rating);
  // A supplier's strongest contributing external factor may follow an internal operating risk.
  // Link to an observation retained on this exact rating, regardless of today's feed availability.
  const reasons = supplier.reasons as ExtendedRiskReason[];
  const inputs = supplier.reasons.find(reason => reason.signal_inputs)?.signal_inputs ?? {};
  const points = reasons.find(reason => reason.breakdown)?.breakdown ?? {};
  const factorPoints = (factor: string) => points[factor] ?? reasons.find(r => r.factor === factor)?.points ?? 0;
  const signal = Object.entries(inputs).sort(([a],[b]) => factorPoints(b) - factorPoints(a) || a.localeCompare(b))
    .flatMap(([, sources]) => sources).find(source => source.title);
  const href = `/observability?agent=risk&tier=supplier&entity=${encodeURIComponent(supplier.entity_id)}${signal ? "#risk-signals" : ""}`;
  const change = supplier.entity_id === changedId ? updated?.latest_grade_change : undefined;
  return <section aria-labelledby="supplier-risk-watch" className="border-l-[3px] border-l-act bg-act-soft/55 px-4 py-3 sm:px-5">
    <p className="mb-1 text-xs font-medium text-muted">Supplier watch</p>
    <div className="flex flex-wrap items-baseline gap-x-3 gap-y-0.5">
      <h2 id="supplier-risk-watch" className="text-base font-bold tracking-tight">
        {name || <RegisterName key={supplier.entity_id} supplierId={supplier.entity_id} />}
      </h2>
      <span className={`text-xs font-semibold tabular ${supplier.grade >= 5 ? "text-stop" : supplier.grade >= 4 ? "text-warn" : "text-muted"}`}>
        {band ? `${band.label} risk` : supplier.rating}{score != null && ` · ${score}/100`}
      </span>
      {waiting && <span className="text-xs text-warn">Last available rating</span>}
    </div>
    {signal && <p className="mt-1 line-clamp-2 text-sm leading-snug">{signal.title}
      <span className="ml-1.5 text-xs text-muted">· {sourceLabel(signal)}{signal.last_known_at && " · Last known"}</span>
    </p>}
    <div className="mt-2 flex flex-wrap items-baseline gap-x-4 gap-y-1">
      <Link href={href} className={linkStyle}>{signal ? "See rating and evidence" : "See rating details"} →</Link>
      {change && updated && <Link href={actionHref(updated.key)} scroll={false} className={linkStyle}>
        Review action for the {change.previous} → {change.current} change
      </Link>}
    </div>
  </section>;
}

export function PackingOutlook({ editions, waiting, replayEnabled }: {
  editions: Forecast[]; waiting: boolean; replayEnabled: boolean;
}) {
  return <section aria-labelledby="outlook">
    <div className="mb-2 flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
      <h2 className="text-base font-bold tracking-tight" id="outlook">Packing forecast</h2>
      <Link href="/observability?agent=planner" className={linkStyle}>Full forecast →</Link>
    </div>
    {waiting && editions.length > 0 && <p className="mb-1 text-xs text-warn">Last available forecast</p>}
    {editions.length ? <table className="w-full text-left text-sm tabular">
      <caption className="sr-only">Expected packing result by the customer deadline, in copies</caption>
      <thead className="border-b border-rule text-xs text-muted"><tr>
        <th scope="col" className="pb-1 font-normal">Edition</th><th scope="col" className="pb-1 text-right font-normal">Copies by close</th>
        <th scope="col" className="pb-1 pl-2 text-right font-normal">Shortfall</th>
      </tr></thead>
      <tbody>{editions.map(f => <tr key={f.product_id} className="border-b border-rule/65 last:border-0">
        <th scope="row" className="py-2 pr-1 font-medium">{f.edition}
          {editions.some(other => other.product_id !== f.product_id && other.edition === f.edition) && ` (${f.product_id})`}</th>
        <td className="py-2 text-right font-semibold">{f.projected_packed == null ? "Pending" : fmt(f.projected_packed)}</td>
        <td className={`py-2 pl-2 text-right font-semibold ${f.shortfall == null ? "text-muted" : f.shortfall > 0 ? "text-stop" : "text-ok"}`}>
          {f.shortfall == null ? "Unknown" : f.shortfall > 0 ? fmt(f.shortfall) : "On target"}
        </td>
      </tr>)}</tbody>
    </table> : <p className="text-sm text-muted">{waiting ? "Waiting for the latest forecast." : replayEnabled
      ? "Play a data drop to see the packing forecast." : "A dated launch snapshot and shift calendar are needed to forecast packing."}</p>}
  </section>;
}
