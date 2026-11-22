import Link from "next/link";
import { useEffect, useState } from "react";
import { actionHref, findingHref, mailHref } from "./buyer-links";
import { fmt, getData, type Forecast, type InboxMessage, type Issue, type OutboxMessage, type RiskScore, type ExtendedRiskReason } from "@/lib/api";
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
const panel = "min-w-0 rounded-lg border border-rule bg-surface px-4 py-4 sm:px-5";
const title = "text-base font-bold tracking-tight";
const header = "mb-3 flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1";

type Signal = NonNullable<RiskScore["reasons"][number]["signal_inputs"]>[string][number];

function sourceLabel(signal: Signal) {
  if (signal.live === false) return "Offline example";
  if (signal.provider === "portal_news") return "Customer news record";
  if (signal.provider === "telemetry") return "Shipment tracking";
  if (signal.provider === "polymarket") return "Polymarket";
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

export function SupplierRiskWatch({ scores, queue, waiting }: {
  scores: RiskScore[]; queue: BuyerQueueEntry[] | null; waiting: boolean;
}) {
  const supplier = scores.filter(row => row.entity_type === "supplier").sort((a,b) =>
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
  return <section aria-labelledby="supplier-risk-watch"
    className="rounded-lg border border-rule border-l-[3px] border-l-act bg-act-soft/60 px-4 py-3 sm:px-5">
    <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
      <h2 id="supplier-risk-watch" className="text-base font-bold tracking-tight">Supplier risk</h2>
      <Link href={href} className={linkStyle}>{signal ? "See rating and evidence" : "See rating details"} →</Link>
    </div>
    <div className="mt-1.5 flex flex-wrap items-baseline gap-x-3 gap-y-1 text-sm">
      <strong className="text-ink">{name || <RegisterName key={supplier.entity_id} supplierId={supplier.entity_id} />}</strong>
      <span className={`font-semibold tabular ${supplier.grade >= 5 ? "text-stop" : supplier.grade >= 4 ? "text-warn" : "text-muted"}`}>
        {score != null && `${score}/100 · `}{supplier.rating}{band && ` · ${band.label}`}</span>
      {waiting && <span className="text-xs text-warn">Last available rating</span>}
    </div>
    {signal && <p className="mt-1 line-clamp-2 text-sm leading-snug">
      <span className="text-muted">{sourceLabel(signal)}{signal.last_known_at && " · Last known"} · </span>{signal.title}
    </p>}
  </section>;
}

export function PackingOutlook({ editions, waiting, replayEnabled }: {
  editions: Forecast[]; waiting: boolean; replayEnabled: boolean;
}) {
  return <section className={panel} aria-labelledby="outlook">
    <div className={header}>
      <h2 className={title} id="outlook">Packing forecast</h2>
      <Link href="/observability?agent=planner" className={linkStyle}>See forecast</Link>
    </div>
    {waiting && editions.length > 0 && <p className="mb-2 text-xs text-warn">Last available forecast</p>}
    {editions.length ? <ul className="space-y-4">{editions.map(f => {
      const target = f.projected_packed != null && f.shortfall != null ? f.projected_packed + f.shortfall : null;
      const edition = editions.some(other => other.product_id !== f.product_id && other.edition === f.edition)
        ? `${f.edition} (${f.product_id})` : f.edition;
      return <li key={f.product_id}>
        <div className="flex flex-wrap items-baseline justify-between gap-x-2">
          <h3 className="text-sm font-semibold">{edition}</h3>
          <span className={`text-xs font-semibold tabular ${f.shortfall == null ? "text-muted" : f.shortfall > 0 ? "text-stop" : "text-ok"}`}>
            {f.shortfall == null ? "Gap unknown" : f.shortfall > 0 ? `${fmt(f.shortfall)} copies short` : "On target"}
          </span>
        </div>
        <p className="mt-0.5 text-sm text-muted">
          {f.projected_packed == null ? "Packing projection pending." : <><strong className="font-semibold text-ink tabular">{fmt(f.projected_packed)}</strong>
            {target != null && ` of ${fmt(target)}`} copies expected by close.</>}
        </p>
        {target != null && target > 0 && f.projected_packed != null && <div className="mt-1.5 h-1 overflow-hidden rounded-sm bg-rule" aria-hidden>
          <div className="h-full bg-[#7b9082]" style={{width:`${Math.min(100,Math.max(0,f.projected_packed / target * 100))}%`}} />
        </div>}
      </li>;
    })}</ul> : <p className="text-sm text-muted">{waiting ? "Waiting for the latest forecast." : replayEnabled
      ? "Play a data drop to see the packing forecast." : "A dated launch snapshot and shift calendar are needed to forecast packing."}</p>}
  </section>;
}

export function RecoveryOutlook({ queue, waiting }: {
  queue: BuyerQueueEntry[] | null; waiting: boolean;
}) {
  // Follow the Planner's ordering. A second way to release the same shipment is an alternative.
  const shipments = new Set<string>();
  const options = (queue ?? []).filter(entry => {
    if (!entry.on_time_recovery || entry.option.available === false || (entry.option.copies_gained ?? 0) <= 0) return false;
    if (entry.shared_shipment) {
      const action = entry.option.actions[0];
      const scope = JSON.stringify([entry.edition, entry.shared_shipment, action?.po_number, action?.po_line]);
      if (shipments.has(scope)) return false;
      shipments.add(scope);
    }
    return true;
  }).slice(0, 2);
  // Also surface the highest-ranked longer-term review with customer-backed order exposure.
  const future = queue?.find(entry => entry.option.horizon === "future" && !entry.option.followup_for &&
    entry.option.available !== false && entry.option.exposure?.orders.length);
  const futureOrders = future?.option.exposure?.orders.length ?? 0;
  return <section className={panel} aria-labelledby="recovery">
    <div className={header}>
      <h2 className={title} id="recovery">Recovery options</h2>
      <Link href="/observability?agent=risk#risk-scenarios" className={linkStyle}>Compare scenarios</Link>
    </div>
    {!waiting && options.length ? <ul className="divide-y divide-rule">
      {options.map(entry => <li key={entry.key} className="first:pt-0 last:pb-0 py-2">
        <Link href={actionHref(entry.key)} scroll={false} className="block w-full text-left text-sm leading-snug hover:underline">
          <span className="block font-semibold text-act">{entry.option.label}</span>
          <span className="mt-0.5 block text-xs text-muted">Up to {fmt(entry.option.copies_gained)} additional {entry.edition} copies if confirmed</span>
        </Link>
      </li>)}
    </ul> : <p className="text-sm text-muted">{waiting || queue == null ? "Waiting for current recovery options." :
      "No current option is projected to recover additional copies by close."}</p>}
    {!waiting && future && <div className="mt-3 border-t border-rule pt-2">
      <p className="text-xs text-muted">Future supply · {fmt(futureOrders)} order {futureOrders === 1 ? "line" : "lines"} to review</p>
      <Link href={actionHref(future.key)} scroll={false} className="mt-0.5 text-left text-sm font-semibold leading-snug text-act hover:underline">{future.option.label}</Link>
    </div>}
  </section>;
}

export function OperationsSummary({ issues, outbox, inbox, deliveries, issuesReady, mailReady, repliesReady }: {
  issues: Issue[]; outbox: OutboxMessage[]; inbox: InboxMessage[]; deliveries: Map<number, Delivery>;
  issuesReady: boolean; mailReady: boolean; repliesReady: boolean;
}) {
  const active = issues.filter(i => i.status !== "resolved").sort((a,b) =>
    b.severity-a.severity || b.updated_at.localeCompare(a.updated_at) || b.id-a.id);
  const sent = [...deliveries.values()];
  const latest = outbox.filter(m => m.sent_at).sort((a,b) => b.sent_at!.localeCompare(a.sent_at!) || b.id-a.id)[0] ?? outbox[0];
  const counts = [
    [sent.filter(d => d === "sent").length, "sent"],
    [sent.filter(d => d === "simulated").length, "simulated"],
    [outbox.filter(m => m.status === "draft").length, "draft"],
    [outbox.filter(m => m.status === "approved").length, "queued"],
    [outbox.filter(m => m.status === "failed").length, "failed"],
    [sent.filter(d => d === "unverified").length, "with unverified delivery"],
    [outbox.filter(m => m.status === "escalated").length, "escalated"],
  ] as const;
  return <>
    <section className={panel} aria-labelledby="active-findings">
      <div className={header}>
        <h2 className={title} id="active-findings">Open findings</h2>
        <Link href="/?view=findings" className={linkStyle}>See findings</Link>
      </div>
      {issuesReady && active[0] ? <>
        <p className="mb-1 text-sm font-semibold">{fmt(active.length)} open
          {active.some(i => i.severity === 5) && <span className="text-stop"> · {fmt(active.filter(i => i.severity === 5).length)} urgent</span>}</p>
        <Link href={findingHref(active[0].id)} className="block text-sm leading-snug text-act hover:underline">{active[0].summary}</Link>
      </> : <p className="text-sm text-muted">{issuesReady ? "No open findings." : "Waiting for findings."}</p>}
    </section>
    <section className={panel} aria-labelledby="supplier-email">
      <div className={header}>
        <h2 className={title} id="supplier-email">Supplier email</h2>
        <Link href="/?view=emails" className={linkStyle}>See emails</Link>
      </div>
      {mailReady && latest ? <>
        <p className="text-sm font-semibold">{counts.filter(([n, label]) => n > 0 || label === "sent").map(([n,label]) =>
          `${fmt(n)} ${label === "draft" && n > 1 ? "drafts" : label}`).join(" · ")}
          {repliesReady && ` · ${fmt(inbox.length)} ${inbox.length === 1 ? "reply" : "replies"}`}</p>
        <Link href={mailHref(latest.id)} className="mt-1 block max-w-full break-words text-left text-sm leading-snug text-act hover:underline">
          <span className="text-muted">Latest · </span>{latest.subject || "Open supplier thread"}
        </Link>
      </> : <p className="text-sm text-muted">{mailReady ? "No outgoing supplier email." : "Waiting for supplier emails."}
        {repliesReady && inbox.length > 0 && ` ${fmt(inbox.length)} incoming ${inbox.length === 1 ? "reply" : "replies"}.`}</p>}
    </section>
  </>;
}
