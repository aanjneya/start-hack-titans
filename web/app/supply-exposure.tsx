import type { SupplyExposure } from "../../API_CONTRACT";

const fmt = (value: number) => value.toLocaleString("en-GB");
const day = (value: string) => new Intl.DateTimeFormat("en-GB", {day:"numeric", month:"short", timeZone:"UTC"})
  .format(new Date(value + "T00:00:00Z"));

const factorNames: Record<string, string> = {
  trade_news: "Trade news", external_politics: "Markets and political news", regulatory_change: "Regulatory watch",
  telemetry: "Shipment telemetry", weather: "Port weather", supplier_health: "Supplier status and notices",
  internal_politics: "Internal approvals and escalations", transport: "Transport",
};

export function ExposureSummary({ exposure }: { exposure: SupplyExposure }) {
  const { orders, previous_lead_days: before, lead_days: after } = exposure;
  const change = before != null && after != null ? after - before : null;
  const knownUnits = orders.length > 0 && orders.every(o => o.quantity != null);
  const firstDue = orders.map(o => o.promised).filter(Boolean).sort()[0];
  const headline = exposure.signals.find(signal => signal.factor === "trade_news")?.sources.find(source => source.title);
  const marketSignal = exposure.signals.find(signal => signal.sources.some(s => s.evidence.locator?.startsWith("polymarket:")));
  const market = marketSignal?.sources.find(s => s.evidence.locator?.startsWith("polymarket:"));
  const odds = market?.market ? `${market.market.yes_pct}%` : market?.evidence.quote?.match(/^Yes=([\d.]+%)/)?.[1];
  const priorOdds = market?.market?.previous_yes_pct;
  const changedDrivers = exposure.last_grade_change?.drivers.filter(d => d.before !== d.after)
    .sort((a, b) => Math.abs(b.after - b.before) - Math.abs(a.after - a.before)).slice(0, 2);
  return <div className="mt-3 space-y-2">
    {exposure.supplier_rating && <p className="flex flex-wrap items-center gap-x-2 text-sm font-semibold">
      {exposure.supplier} <span className="rounded bg-warn-soft px-2 py-0.5 text-ink">Supplier grade {exposure.last_grade_change && `${exposure.last_grade_change.previous} → `}{exposure.supplier_rating}</span>
    </p>}
    {headline && <p className="text-sm">{headline.title}.</p>}
    {orders.length > 0 || after != null ? <dl className="grid grid-cols-2 gap-x-4 gap-y-3 border-y border-rule py-3 text-sm tabular">
      {after != null && <div><dt className="text-xs text-muted">Supplier lead time</dt><dd className="text-lg font-bold">
        {change != null && change !== 0 && <>{before} → </>}{after} days</dd>
        {change != null && change !== 0 && <dd className="text-xs text-muted">{Math.abs(change)} {Math.abs(change) === 1 ? "day" : "days"} {change > 0 ? "longer" : "shorter"}</dd>}</div>}
      {orders.length > 0 && <div><dt className="text-xs text-muted">{orders.length} open PO {orders.length === 1 ? "line" : "lines"}</dt>
        <dd className="text-lg font-bold">{knownUnits ? `${fmt(orders.reduce((sum, o) => sum + o.quantity!, 0))} ${orders.some(o => o.status?.includes("approval")) ? "scheduled" : "ordered"} units` : "Quantity unconfirmed"}</dd>
        {firstDue && <dd className="text-xs text-muted">First delivery due {day(firstDue)}</dd>}</div>}
    </dl> : <p className="text-sm">{exposure.verification_orders?.length ?
      `Check the supplier’s open PO ${exposure.verification_orders[0].po}/${exposure.verification_orders[0].line ?? "?"} for exposure.` :
      "The buyer needs to establish which order lines the warning covers."}</p>}
    {exposure.order_value && <p className="text-sm"><strong>{exposure.order_value.currency} {fmt(exposure.order_value.amount)}</strong> recorded PO value in scope.</p>}
    {exposure.delivery_history && <p className="text-sm">Past deliveries · <strong>{exposure.delivery_history.on_time} of {exposure.delivery_history.count}</strong> comparable PO lines met their promised date.</p>}
    {exposure.signals.length > 0 && <details className="text-sm text-muted"><summary className="cursor-pointer">
      {exposure.signals.length} signal {exposure.signals.length === 1 ? "type" : "types"}
      {market && <> · Polymarket · {priorOdds != null && `${priorOdds}% → `}{odds && `${odds} yes · `}{market.origin.toLowerCase()}</>}</summary>
      {!!changedDrivers?.length && <p className="mt-2 pl-3 font-medium text-ink">What moved the grade · {changedDrivers.map(d => factorNames[d.factor] || d.factor).join(" and ")}.</p>}
      {market && <div className="mt-1 pl-3"><p>{market.title || marketSignal?.text}</p>
        {market.market?.disruption_outcome === "No" && <p>The question asks about recovery. The rating uses its No side.</p>}
        <p className="text-xs">The price describes this market question. Supplier evidence establishes which orders need review.</p></div>}
      <ul className="mt-1 space-y-1 pl-3">{exposure.signals.map(s => <li key={s.factor}>
        <strong>{factorNames[s.factor] || s.factor}</strong>{s.sources[0]?.scope && ` · ${s.sources[0].scope}`}
      </li>)}</ul></details>}
    {exposure.packing_closes_before_notice && exposure.effective_after && <p className="text-xs text-muted">
      Packing closes {day(exposure.packing_closes_before_notice)}. The supplier notice applies to deliveries after {day(exposure.effective_after)}.</p>}
  </div>;
}

export function ExposureEvidence({ exposure: x }: { exposure: SupplyExposure }) {
  const changed = x.last_grade_change?.drivers.filter(d => d.after !== d.before)
    .sort((a, b) => Math.abs(b.after - b.before) - Math.abs(a.after - a.before));
  return <div className="mt-4 space-y-4">
    {!!x.rating_path && x.rating_path.length > 1 && <section>
      <h4 className="font-bold">How far does the supply risk reach?</h4>
      <ol className="mt-2 divide-y divide-rule">{x.rating_path.map(node => <li key={node.tier + node.id} className="py-1.5">
        <details><summary className="cursor-pointer">
          <span className="capitalize">{node.id === x.assembly ? "Ordered assembly" : node.tier}</span> · {node.name}{" "}
          <strong className="whitespace-nowrap">{node.previous && `${node.previous} → `}{node.rating}</strong>
        </summary>
          <p className="ml-4 mt-1 text-muted">{node.id} · {node.reason || "The Risk Rater recorded this grade."}</p>
        </details>
      </li>)}</ol>
      <p className="mt-2 text-xs text-muted">Grades show each item’s latest risk rating and its preceding distinct grade.
        The packing projection uses line-ready materials.</p>
    </section>}
    {(x.signals.length > 0 || !!x.other_drivers?.length) && <section>
      <h4 className="font-bold">What changed in the supplier rating?</h4>
      {changed && changed.length > 0 && <p className="mt-1">
        At the {x.last_grade_change?.previous} → {x.last_grade_change?.current} grade change, the largest movements were
        {" "}{changed.slice(0, 2).map(d => `${factorNames[d.factor] || d.factor} (${d.before} → ${d.after} points)`).join(" and ")}.
      </p>}
      {x.score_basis && <p className="mt-2 text-sm text-muted">Supplier score · {x.score_basis.total}/100.
        {" "}{x.score_basis.internal} points come from internal records and {x.score_basis.external} from external inputs.
        {x.score_basis.external_raw > x.score_basis.external_cap &&
          ` The external inputs add up to ${x.score_basis.external_raw} points and the model caps them at ${x.score_basis.external_cap}.`}</p>}
      <ul className="mt-2 space-y-2">{x.signals.map(signal => <li key={signal.factor} className="rounded border border-rule p-3">
        <p className="font-semibold">{factorNames[signal.factor] || signal.factor} · {signal.points} points</p>
        <p>{signal.text}</p>
        {x.last_grade_change?.drivers.some(d => d.factor === signal.factor && d.before === d.after && d.after === signal.points)
          && <p className="text-xs text-muted">Contribution at the grade change · {signal.points} points, unchanged</p>}
        {signal.sources.map(({ evidence, origin, published, title, scope, observed_at, market, last_known_at, material_matches }, i) => <div key={i} className="mt-1 text-xs text-muted">
          <p>{origin}{scope && ` · ${scope}`} · {published ? `Published ${published}` : observed_at ? `Source updated ${observed_at}` : "Source date unknown"}</p>
          {origin !== "Offline example" && /^https?:\/\//i.test(evidence.path)
            ? <a className="text-act underline" href={evidence.path} target="_blank" rel="noreferrer">{title || evidence.source_kind || "Source"} ↗</a>
            : <p>{title || evidence.path.split("/").pop()}</p>}
          {evidence.quote && <p className="mt-1">{evidence.quote}</p>}
          {market?.scope_basis && <p>{market.scope_basis}{market.closes_at && ` · Market closes ${market.closes_at.slice(0, 10)}`}</p>}
          {material_matches?.map(m => <p key={m.part}>The publication names “{m.term}”. Your sourcing records link {m.description || m.part} ({m.part}) to this supplier.</p>)}
          {last_known_at && <p className="text-warn">Feed unavailable at this check. Last successful reading {last_known_at.slice(0, 16).replace("T", " ")} UTC.</p>}
        </div>)}
      </li>)}</ul>
      {!!x.other_drivers?.length && <p className="mt-2 text-sm text-muted">
        Other factors in this rating · {x.other_drivers.map(d => factorNames[d.factor] || d.factor).join(" · ")}
      </p>}
    </section>}
    <section>
      <h4 className="font-bold">What connects this to our orders?</h4>
      <p>{x.supplier || "Supplier unconfirmed"}{x.share != null && ` supplies ${fmt(100 * x.share)}% of ${x.component}`}.
        {x.assembly && ` The supplier fits it into ${x.assembly_name || x.assembly} (${x.assembly}).`}</p>
      <p className="text-muted">{x.effective_after ? `Supplier notice covers deliveries after ${x.effective_after}.` : "Effective date unknown."}</p>
      {x.uncertainties?.map(note => <p key={note} className="mt-2 font-semibold text-warn">{note}</p>)}
      {x.orders.length > 0 && <table className="mt-2 w-full text-left text-sm tabular">
        <thead className="text-muted"><tr><th>PO / line</th><th>Promised</th><th className="text-right">Units</th></tr></thead>
        <tbody>{x.orders.map(o => <tr className="border-t border-rule" key={o.po + "/" + o.line}>
          <td>{o.po}/{o.line ?? "?"}</td><td>{o.promised}</td><td className="text-right">{o.quantity == null ? "Unknown" : fmt(o.quantity)}</td>
        </tr>)}</tbody>
      </table>}
      {!!x.verification_orders?.length && <div className="mt-2">
        <h5 className="font-semibold">Open PO lines to check with this supplier</h5>
        <p className="text-muted">Their exposure is unverified. The earliest dated lines are shown first.</p>
        <ul className="mt-1 divide-y divide-rule">{x.verification_orders.map(o => <li className="py-1" key={o.po + "/" + o.line}>
          {o.po}/{o.line ?? "?"} · {o.material || "Material unknown"} · {o.promised || "Delivery date unknown"}</li>)}</ul>
      </div>}
      {x.order_value && <p className="mt-2 font-semibold">Recorded value of these PO lines · {x.order_value.currency} {fmt(x.order_value.amount)}</p>}
      {x.delivery_history && <details className="mt-3 rounded border border-rule px-3 py-2">
        <summary className="cursor-pointer font-semibold text-act">Compare past deliveries for this item</summary>
        <p className="mt-2">{x.delivery_history.material} · {x.delivery_history.site}. {x.delivery_history.on_time} of {x.delivery_history.count} completed lines arrived by their recorded promise, from {x.delivery_history.first} through {x.delivery_history.last}.</p>
        <table className="mt-2 w-full text-left text-xs tabular"><caption className="text-left text-muted">Latest comparable lines</caption>
          <thead><tr><th>PO / line</th><th>Promised</th><th>Delivered</th></tr></thead><tbody>
            {x.delivery_history.recent.map(l => <tr key={l.po + "/" + l.line} className="border-t border-rule">
              <td className="py-1">{l.po}/{l.line}</td><td>{l.promised}</td><td>{l.arrived}</td>
            </tr>)}</tbody></table>
        <p className="mt-2 text-xs text-muted">The supplier, material and receiving site match the open orders. The new supplier terms determine the review.</p>
      </details>}
    </section>
    {x.qualification_gate && <section>
      <h4 className="font-bold">Before a new supplier can deliver</h4>
      <p>Successful {x.qualification_gate.assessment} and written release from these signers are required for serial deliveries.</p>
      <ul className="mt-1">{x.qualification_gate.signers.map(a => <li key={a.required_role}>
        <strong>{a.person || "Assignment pending"}</strong> · {a.signing_role}{a.signing_role !== a.required_role && ` (acting for ${a.required_role})`}
      </li>)}</ul><p className="mt-1 text-xs text-muted">{x.qualification_gate.evidence.path.split("/").pop()} · Effective {x.qualification_gate.effective}</p>
    </section>}
    {!!x.sample_offers?.length && <section className="rounded border-l-4 border-act bg-act-soft px-3 py-2">
      <h4 className="font-bold">An alternative to investigate</h4>
      {x.sample_offers.map(offer => <blockquote className="mt-1" key={offer.source + offer.text}>{offer.text}
        <cite className="block text-xs text-muted">{offer.source.split("/").pop()}</cite>
      </blockquote>)}
      <p className="mt-2">The incumbent offers these samples. Engineering fit, Quality release, production capacity and an independent supplier still need verification.</p>
    </section>}
  </div>;
}
