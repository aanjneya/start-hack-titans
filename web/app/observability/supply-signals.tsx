"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import type { AgentLogEntry, BuyerQueueEntry, HealthCheckResponse, RiskScore } from "../../../API_CONTRACT";
import { getData, parseTs } from "@/lib/api";
import { actionHref } from "../buyer-links";
import { recordedScore, riskBands } from "./risk-explanations";

type Source = NonNullable<RiskScore["reasons"][number]["signal_inputs"]>[string][number];
type MarketSource = Source & { market: NonNullable<Source["market"]> };
type Signals = HealthCheckResponse["lastRiskCheck"];
const factorLabels: Record<string, string> = {
  trade_news: "Trade news", regulatory_change: "Regulatory watch", telemetry: "Shipment tracking",
  weather: "Route weather", external_politics: "Market and political news", supplier_health: "supplier status and notices",
  transport: "shipping exposure", internal_politics: "internal approvals",
};
const days = new Intl.DateTimeFormat("en-GB", { timeZone: "UTC", dateStyle: "medium" });
const times = new Intl.DateTimeFormat("en-GB", { timeZone: "UTC", dateStyle: "medium", timeStyle: "short" });

function dateLabel(value: string | null | undefined): string | null {
  if (!value) return null;
  const date = parseTs(value);
  if (!date || Number.isNaN(date.getTime())) return null;
  return value.length === 10 ? days.format(date) : times.format(date) + " UTC";
}

function sourceKind(source: Source): string {
  if (source.live === false) return "Offline example";
  if (source.provider === "portal_news") return "Customer news record";
  if (source.provider === "telemetry") return "Customer shipment record";
  if (source.provider === "weather") return "Weather observation";
  if (source.provider === "gnews" || source.provider === "gdelt") return "News coverage";
  if (source.provider === "polymarket") return "Public market reading";
  let host = "";
  try { host = new URL(source.evidence.path).hostname; } catch { /* A local file can also be evidence. */ }
  if (/(^|\.)(federalregister\.gov|eur-lex\.europa\.eu|wto\.org)$/i.test(host)) return "Official publication";
  if (host === "news.google.com") return "News coverage";
  return source.evidence.source_kind || "Source type unknown";
}

function marketsFor(sources: Source[]): MarketSource[] {
  return sources.filter((s): s is MarketSource =>
    s.provider === "polymarket" && !!s.market && Number.isFinite(s.market.yes_pct)
    && s.market.yes_pct >= 0 && s.market.yes_pct <= 100);
}

function marketKey(source: MarketSource) {
  return JSON.stringify([source.live, source.market.question_id, source.title, source.evidence.path]);
}

function MarketPrice({ source, unavailable }: { source: MarketSource; unavailable?: string }) {
  const previous = source.market.previous_yes_pct;
  const yes = source.market.yes_pct;
  const no = Number((100 - yes).toFixed(1));
  const side = source.market.disruption_outcome;
  const odds = side === "No" ? no : yes;
  const delta = previous == null ? null : Number((yes - previous).toFixed(1));
  const external = source.live && /^https?:\/\//i.test(source.evidence.path);
  const date = dateLabel(source.observed_at) || dateLabel(source.published);

  return <li className="signal-market border-b border-rule bg-act-soft/50 px-4 py-3 last:border-b-0 sm:px-5">
    <p className="text-xs font-semibold text-act">Polymarket · {sourceKind(source)}</p>
    <p className="mt-1 text-sm leading-snug text-ink">
      {external ? <a href={source.evidence.path} className="font-medium underline decoration-rule underline-offset-2" rel="noreferrer" target="_blank">{source.title} ↗</a> : source.title}
      {" "}<strong className="whitespace-nowrap">{yes}% Yes</strong>
    </p>
    <p className="mt-1 text-xs text-muted">
      {side === "No" && <>The disruption outcome is No ({odds}%). </>}
      {delta === 0 && "Unchanged since the previous rating. "}
      {delta != null && delta !== 0 && `Previously ${previous}% Yes. `}
      {source.scope && <>{source.scope} · </>}{dayOrUnknown(date)}
      {source.market.closes_at && ` · Closes ${dateLabel(source.market.closes_at) || "date unknown"}`}
    </p>
    {source.market.scope_basis && <p className="mt-1 text-xs text-muted">{source.market.scope_basis}</p>}
    {(unavailable || source.last_known_at) && <p className="mt-1 text-xs text-warn">
      Market feed unavailable at the latest check.{source.last_known_at && ` Last successful reading · ${dateLabel(source.last_known_at)}.`}</p>}
  </li>;
}

function dayOrUnknown(date: string | null) {
  return date ? "Source date " + date : "Source date unknown";
}

function SourceEvidence({ source }: { source: Source }) {
  const ext = source.live && /^https?:\/\//i.test(source.evidence.path);
  const date = dateLabel(source.published) || dateLabel(source.observed_at);
  const title = source.title || source.summary || source.evidence.path || "Untitled source";
  return <li className="min-w-0 break-words text-sm">
    <span className="mr-1.5 text-xs font-semibold text-muted">{sourceKind(source)}</span>
    {ext ? <a href={source.evidence.path} target="_blank" rel="noreferrer" className="text-act underline underline-offset-2">{title} ↗</a> : title}
    {date && <span className="ml-1 text-xs text-muted">· {date}</span>}
    {source.last_known_at && <span className="ml-1 text-xs text-warn">· Last successful reading {dateLabel(source.last_known_at)}</span>}
  </li>;
}

function SupplierSignals({ row, action, check, change }: {
  row: RiskScore; action?: BuyerQueueEntry; check: Signals; change?: AgentLogEntry;
}) {
  const inputs = row.reasons.find(reason => reason.signal_inputs)?.signal_inputs ?? {};
  const markets = marketsFor(inputs.external_politics ?? []);
  const priced = new Set<Source>(markets);
  const otherSources = Object.values(inputs).flat().filter(source => !priced.has(source));
  const oldGrade = riskBands[Number(change?.data?.old_grade) - 1]?.rating;
  const transition = action?.option.exposure?.last_grade_change;
  const drivers = transition?.current === row.rating && transition.previous === oldGrade
    ? transition.drivers.filter(d => d.before !== d.after).sort((a, b) => Math.abs(b.after - b.before) - Math.abs(a.after - a.before)).slice(0, 2) : [];
  const marketPoints = transition?.drivers.find(d => d.factor === "external_politics");
  const basis = (row.reasons[0] as {score_basis?: {internal: number; external: number; external_raw: number; external_cap: number; total: number}} | undefined)?.score_basis;
  return <>
    {markets.length > 0 ? <ul aria-label="Recorded market questions">
      {markets.map(s => <MarketPrice key={marketKey(s)} source={s} unavailable={check?.unavailable?.polymarket?.reason} />)}
    </ul> : <p className="bg-dock/60 px-4 py-3 text-sm text-muted">This supplier’s rating has no recorded Polymarket price.</p>}
    <div className="space-y-3 border-t border-rule px-4 py-3 sm:px-5">
      {otherSources.length > 0 && <ul aria-label="Other recorded sources" className="space-y-2">
        {otherSources.map((source, index) => <SourceEvidence key={index} source={source} />)}
      </ul>}
      {oldGrade && change && <div className="text-xs text-muted">
        <p><span className="font-semibold text-ink">Supplier grade {oldGrade} → {row.rating}</span> · Recorded {dateLabel(change.ts)}</p>
        {!!drivers.length && <p className="mt-1">Largest changes · {drivers.map(d => `${factorLabels[d.factor] || d.factor.replaceAll("_", " ")} (${d.after > d.before ? "+" : ""}${Number((d.after - d.before).toFixed(1))} points)`).join(" and ")}.
          {marketPoints && marketPoints.before === marketPoints.after && ` Market and political news stayed at ${marketPoints.after} points.`}</p>}
      </div>}
      {basis && basis.total === recordedScore(row) && <p className="text-xs text-muted">Of {basis.total} risk points, {basis.internal} come from purchasing and shipping records and {basis.external} from outside signals.
        {basis.external_raw > basis.external_cap && ` The outside signals reached their ${basis.external_cap}-point cap.`}</p>}
    </div>
  </>;
}

function SupplierAction({ supplier, action, waiting, queueAvailable, retry }: {
  supplier?: string; action?: BuyerQueueEntry; waiting: boolean; queueAvailable: boolean; retry: () => void;
}) {
  const exposure = action?.option.exposure;
  const firstOrder = exposure?.orders[0];
  const signers = action?.option.assignments?.map(a => a.person || a.required_role) ?? [];
  return <section id="risk-action" aria-labelledby="risk-action-heading"
    className="signal-buyer-action min-w-0 scroll-mt-24 rounded-xl border border-rule bg-surface p-4 sm:p-5 self-start">
    <header className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
      <h2 id="risk-action-heading" className="text-xl font-bold tracking-tight">Buyer action</h2>
      {supplier && <p className="text-sm font-semibold text-muted">{supplier}</p>}
    </header>
    {action ? <>
      <Link href={actionHref(action.key)} className="mt-2 inline-block text-base font-bold text-act underline decoration-1 underline-offset-4">{action.option.label}</Link>
      <div className="mt-2 space-y-1 text-sm text-muted">
        {exposure?.previous_lead_days != null && exposure.lead_days != null && exposure.lead_days !== exposure.previous_lead_days &&
          <p>Supplier lead time changed from {exposure.previous_lead_days} to {exposure.lead_days} days.</p>}
        {firstOrder && <p>{exposure!.orders.length} open order {exposure!.orders.length === 1 ? "line" : "lines"} · PO {firstOrder.po}/{firstOrder.line ?? "?"}
          {firstOrder.promised && ` due ${dateLabel(firstOrder.promised)}`}</p>}
        {!firstOrder && <p>Check whether this warning applies to the supplier.</p>}
        {signers.length > 0 && <p><span className="font-semibold text-ink">Approval</span> · {signers.join(" + ")}</p>}
      </div>
    </> : <p className="mt-2 text-sm text-muted">{waiting ? "Checking current buyer actions…" : queueAvailable
      ? "No open buyer action is linked to this supplier’s rating." : <>Buyer actions are unavailable.{" "}
        <button onClick={retry} className="font-semibold text-act underline">Retry</button></>}</p>}
  </section>;
}

export function SupplySignals({ scores, names, preferredSupplier, check, trace, version }: {
  scores: RiskScore[]; names: Record<string, string>; preferredSupplier: string; check: Signals; trace: AgentLogEntry[]; version?: string;
}) {
  const [attempt, setAttempt] = useState(0);
  const [queue, setQueue] = useState<{ version?: string; entries: BuyerQueueEntry[] | null } | null>(null);
  useEffect(() => {
    let active = true;
    getData<BuyerQueueEntry[]>("/api/buyer-queue").then(entries => {
      if (active) setQueue({version, entries});
    }).catch(() => { if (active) setQueue({version, entries:null}); });
    return () => {active = false;};
  }, [version, attempt]);

  const suppliers = scores.filter(s => s.entity_type === "supplier")
    .sort((a,b) => (recordedScore(b) ?? -1) - (recordedScore(a) ?? -1) || a.entity_id.localeCompare(b.entity_id));
  const row = preferredSupplier ? suppliers.find(s => s.entity_id === preferredSupplier) : suppliers[0];
  const supplierName = row && (names[row.entity_id] || row.entity_id);
  const currentQueue = queue?.version === version ? queue?.entries : null;
  const entries = currentQueue?.filter(e => e.option.available !== false && e.option.exposure
    && e.option.actions.some(a => a.supplier_id === row?.entity_id)) ?? [];
  const change = trace.find(log => log.agent === "risk" && log.action === "grade_changed" && log.data?.entity_type === "supplier" && log.data.entity_id === row?.entity_id);
  // Prefer the first ranked review backed by an identified order.
  const action = entries.find(e => e.option.exposure?.orders.length) ?? entries[0];
  return <>
    <section id="risk-signals" aria-labelledby="supply-signals-heading" className="min-w-0 scroll-mt-24 self-start overflow-hidden rounded-xl border border-rule bg-surface">
      <header className="flex flex-wrap items-center justify-between gap-3 border-b border-rule px-4 py-3 sm:px-5">
        <h2 id="supply-signals-heading" className="text-xl font-bold tracking-tight">Supply signals</h2>
        <Link href="/?view=risk" className="text-xs font-semibold text-act underline">Review signal sources</Link>
      </header>
      {row ? <>
        <p className="px-4 py-2 text-sm sm:px-5"><strong>{supplierName}</strong> <span className="text-muted">· {row.rating}</span></p>
        <SupplierSignals key={row.entity_id} row={row} action={action} check={check}
          change={change?.data?.new_grade === row.grade ? change : undefined} />
      </> : <p className="px-4 py-5 text-sm text-muted">Supply signals appear when a supplier rating has been recorded.</p>}
    </section>
    <SupplierAction supplier={supplierName} action={action}
      retry={() => { setQueue(null); setAttempt(n => n + 1); }}
      waiting={!queue || queue.version !== version} queueAvailable={currentQueue != null} />
  </>;
}
