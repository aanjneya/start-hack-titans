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

function marketsFor(score: RiskScore): MarketSource[] {
  return (score.reasons[0]?.signal_inputs?.external_politics ?? []).filter((s): s is MarketSource =>
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

  return <div className="signal-market bg-act-soft/70 px-4 py-4 sm:px-5">
    <div className="flex flex-wrap items-center justify-between gap-2 text-xs font-semibold">
      <span className="tracking-wide text-act">Polymarket</span>
      <span className="rounded border border-rule bg-surface px-2 py-1 text-muted">{sourceKind(source)}</span>
    </div>
    <h3 className="mt-3 text-lg font-semibold leading-snug text-ink">
      {external ? <a href={source.evidence.path} className="underline decoration-rule underline-offset-4 hover:decoration-current" rel="noreferrer" target="_blank">{source.title} ↗</a> : source.title}
    </h3>
    <figure className="mt-3" aria-label={`Recorded market price, Yes ${yes} percent, No ${no} percent`}>
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <strong className="signal-odds figure text-4xl leading-none text-ink">{yes}%</strong>
        <span className="text-sm font-semibold text-ink">Yes</span>
        {delta == null && <span className="text-xs text-muted">Earlier price unavailable</span>}
        {delta === 0 && <span className="text-xs text-muted">Unchanged from the previous recorded rating</span>}
        {delta != null && delta !== 0 && <span className="text-xs text-muted">{delta > 0 ? "+" : ""}{delta} points from {previous}% Yes at the previous recorded rating</span>}
      </div>
      <div className="mt-3 flex h-2 overflow-hidden rounded-sm bg-rule" aria-hidden>
        <span className="h-full bg-act" style={{width: yes + "%"}} />
      </div>
      <figcaption className="mt-1 flex justify-between text-xs text-muted"><span>Yes {yes}%</span><span>No {no}%</span></figcaption>
    </figure>
    <p className="mt-3 text-sm text-ink">{side === "No" ? `This question asks about recovery. The rating uses the No price of ${odds}% to measure disruption.` :
      "A Yes outcome signals greater disruption."}</p>
    <p className="mt-1 text-xs text-muted">{source.scope || "Scope unknown"} · {dayOrUnknown(date)}
      {source.market.closes_at && ` · Closes ${dateLabel(source.market.closes_at) || "date unknown"}`}</p>
    {source.market.scope_basis && <p className="mt-1 text-xs text-muted">{source.market.scope_basis}.</p>}
    {(unavailable || source.last_known_at) && <p className="mt-2 text-xs font-semibold text-warn">
      Market feed unavailable at the latest check.{source.last_known_at && ` Last successful reading · ${dateLabel(source.last_known_at)}.`}</p>}
  </div>;
}

function dayOrUnknown(date: string | null) {
  return date ? "Source date " + date : "Source date unknown";
}

function SourceEvidence({ source }: { source: Source }) {
  const ext = source.live && /^https?:\/\//i.test(source.evidence.path);
  const date = dateLabel(source.published) || dateLabel(source.observed_at);
  return <div className="min-w-0 border-l border-rule pl-3 text-sm">
    <p className="font-medium">
      {ext ? <a href={source.evidence.path} target="_blank" rel="noreferrer" className="text-act underline">{source.title || source.summary} ↗</a> : source.title || source.summary}
    </p>
    <p className="mt-0.5 text-xs text-muted">{sourceKind(source)} · {source.scope || "Scope unknown"} · {dayOrUnknown(date)}</p>
    {source.evidence.quote && <p className="mt-1 text-xs text-muted">{source.evidence.quote}</p>}
    {!ext && source.evidence.path && <p className="mt-1 break-all text-xs text-muted">{source.evidence.path}</p>}
    {source.last_known_at && <p className="mt-1 text-xs text-warn">Last successful reading · {dateLabel(source.last_known_at)}</p>}
  </div>;
}

function SupplierSignals({ row, action, check, change }: {
  row: RiskScore; action?: BuyerQueueEntry; check: Signals; change?: AgentLogEntry;
}) {
  const [chosenMarket, setChosenMarket] = useState("");
  const markets = marketsFor(row);
  const market = markets.find(item => marketKey(item) === chosenMarket) ?? markets[0];
  const inputs = row.reasons[0]?.signal_inputs ?? {};
  const groups = Object.entries(inputs).map(([factor, sources]) => ({
    factor, sources: sources.filter(s => s.provider !== "polymarket" || !s.market),
  })).filter(g => g.sources.length);
  const leadEvidence = groups.flatMap(g => g.sources).find(s => sourceKind(s) === "Official publication")
    ?? groups.find(g => g.factor === "trade_news")?.sources[0] ?? groups[0]?.sources[0];
  const oldGrade = riskBands[Number(change?.data?.old_grade) - 1]?.rating;
  const transition = action?.option.exposure?.last_grade_change;
  const drivers = transition?.current === row.rating && transition.previous === oldGrade
    ? transition.drivers.filter(d => d.before !== d.after).sort((a, b) => Math.abs(b.after - b.before) - Math.abs(a.after - a.before)).slice(0, 2) : [];
  const marketPoints = transition?.drivers.find(d => d.factor === "external_politics");
  const basis = (row.reasons[0] as {score_basis?: {internal: number; external: number; external_raw: number; external_cap: number; total: number}} | undefined)?.score_basis;
  return <>
    {market ? <div>
      <MarketPrice source={market} unavailable={check?.unavailable?.polymarket?.reason} />
      {markets.length > 1 && <label className="flex items-center gap-3 border-t border-rule bg-act-soft/70 px-4 py-2 text-xs sm:px-5">
        <span className="shrink-0 font-semibold text-act">Market question</span>
        <select value={marketKey(market)} onChange={e => setChosenMarket(e.target.value)}
          className="min-w-0 flex-1 truncate rounded border border-rule bg-surface px-2 py-1.5">
          {markets.map((s, i) => <option key={marketKey(s)} value={marketKey(s)}>{i + 1}. {s.title}</option>)}
        </select>
      </label>}
    </div> : <p className="bg-dock/60 px-4 py-3 text-sm text-muted">This supplier’s rating has no recorded Polymarket price.</p>}

    <div className="space-y-3 border-t border-rule px-4 py-3 sm:px-5">
      {groups.length > 0 ? <details>
        <summary className="cursor-pointer text-sm text-ink">
          <span className="font-semibold">Other evidence · {groups.length} {groups.length === 1 ? "source group" : "source groups"}</span>
          {leadEvidence && <span className="mt-1 block"><span className="font-semibold text-act">{sourceKind(leadEvidence)}</span> · {leadEvidence.title || leadEvidence.summary}</span>}
        </summary>
        <div className="mt-3 space-y-4">{groups.map(({factor, sources}) => <div key={factor}>
          <p className="mb-1.5 text-xs font-semibold text-muted">{factorLabels[factor] || factor.replaceAll("_", " ")}</p>
          <div className="space-y-2">{sources.map((s, i) => <SourceEvidence key={i} source={s} />)}</div>
        </div>)}</div>
      </details> : <p className="text-xs text-muted">No other outside sources were retained in this rating.</p>}
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
    className="signal-buyer-action min-w-0 scroll-mt-24 rounded-xl border border-rule bg-surface p-4 sm:p-5 lg:col-start-1 lg:row-start-2">
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
  const [choice, setChoice] = useState(preferredSupplier);
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
  const row = suppliers.find(s => s.entity_id === choice) ?? suppliers.find(s => Object.keys(s.reasons[0]?.signal_inputs ?? {}).length) ?? suppliers[0];
  const currentQueue = queue?.version === version ? queue?.entries : null;
  const entries = currentQueue?.filter(e => e.option.available !== false && e.option.exposure
    && e.option.actions.some(a => a.supplier_id === row?.entity_id)) ?? [];
  const change = trace.find(log => log.agent === "risk" && log.action === "grade_changed" && log.data?.entity_type === "supplier" && log.data.entity_id === row?.entity_id);
  // Prefer the first ranked review backed by an identified order.
  const action = entries.find(e => e.option.exposure?.orders.length) ?? entries[0];
  return <>
    <section id="risk-signals" aria-labelledby="supply-signals-heading" className="min-w-0 scroll-mt-24 self-start overflow-hidden rounded-xl border border-rule bg-surface lg:col-start-2 lg:row-span-2 lg:row-start-1">
      <header className="flex flex-wrap items-center justify-between gap-3 border-b border-rule px-4 py-3 sm:px-5">
        <h2 id="supply-signals-heading" className="text-xl font-bold tracking-tight">Supply signals</h2>
        <Link href="/?view=risk" className="text-xs font-semibold text-act underline">Review signal sources</Link>
      </header>
      {row ? <>
        <div className="flex items-center gap-3 px-4 py-3 sm:px-5">
          <label className="shrink-0 text-xs font-semibold text-muted" htmlFor="signal-supplier">Supplier</label>
          <select id="signal-supplier" value={row.entity_id} onChange={e => setChoice(e.target.value)}
            className="min-w-0 flex-1 truncate rounded-md border border-rule bg-surface px-2 py-1 text-sm font-semibold">
            {suppliers.map(s => <option key={s.entity_id} value={s.entity_id}>{names[s.entity_id] || s.entity_id} · {s.rating}</option>)}
          </select>
        </div>
        <SupplierSignals key={row.entity_id} row={row} action={action} check={check}
          change={change?.data?.new_grade === row.grade ? change : undefined} />
      </> : <p className="px-4 py-5 text-sm text-muted">Supply signals appear when a supplier rating has been recorded.</p>}
    </section>
    <SupplierAction supplier={row && (names[row.entity_id] || row.entity_id)} action={action}
      retry={() => { setQueue(null); setAttempt(n => n + 1); }}
      waiting={!queue || queue.version !== version} queueAvailable={currentQueue != null} />
  </>;
}
