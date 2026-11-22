import type { SupplyExposure } from "../../API_CONTRACT";

const fmt = (value: number) => value.toLocaleString("en-GB");

const factorNames: Record<string, string> = {
  trade_news: "Trade news", external_politics: "Markets and political news", regulatory_change: "Regulatory watch",
  telemetry: "Shipment telemetry", weather: "Port weather", supplier_health: "Supplier status and notices",
  internal_politics: "Internal approvals and escalations", transport: "Transport",
};

export function ExposureSummary({ exposure }: { exposure: SupplyExposure }) {
  const { orders, previous_lead_days: before, lead_days: after, assembly } = exposure;
  const change = before != null && after != null ? after - before : null;
  const knownUnits = orders.length > 0 && orders.every(o => o.quantity != null);
  const firstDue = orders.map(o => o.promised).filter(Boolean).sort()[0];
  const headline = exposure.signals.find(signal => signal.factor === "trade_news")?.sources.find(source => source.title);
  const marketSignal = exposure.signals.find(signal => signal.sources.some(s => s.evidence.locator?.startsWith("polymarket:")));
  const market = marketSignal?.sources.find(s => s.evidence.locator?.startsWith("polymarket:"));
  const odds = market?.evidence.quote?.match(/^Yes=([\d.]+%)/)?.[1];
  return <div className="mt-3 space-y-2">
    {exposure.supplier_rating && <p className="text-sm font-semibold text-warn">
      {exposure.supplier} · Supplier grade {exposure.last_grade_change && `${exposure.last_grade_change.previous} → `}{exposure.supplier_rating}
    </p>}
    {headline && <p className="text-sm">{headline.title}.</p>}
    <p>{orders.length > 0
      ? <><strong>{knownUnits ? `${fmt(orders.reduce((sum, o) => sum + o.quantity!, 0))} ordered units` : "Orders in scope"}</strong>
        {` across ${orders.length} open PO ${orders.length === 1 ? "line" : "lines"}`}{firstDue &&
          `, first due ${new Intl.DateTimeFormat("en-GB", {day:"numeric",month:"short",year:"numeric", timeZone:"UTC"}).format(new Date(firstDue + "T00:00:00Z"))}`}.</>
      : "The buyer needs to establish which order lines the warning covers."}
      {change != null && change > 0 ? ` Lead time grew from ${before} to ${after} days (+${change}).` :
        after != null ? ` Recorded lead time is ${after} days.` : ""}</p>
    {market && <details className="text-sm text-muted"><summary className="cursor-pointer">
      Polymarket · {odds && `${odds} yes · `}{market.origin.toLowerCase()}</summary>
      <p className="pl-3">{marketSignal?.text}</p></details>}
    {assembly && <p className="text-sm text-muted">The supplier fits this part into the ordered assemblies before delivery.</p>}
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
      <ul className="mt-2 space-y-2">{x.signals.map(signal => <li key={signal.factor} className="rounded border border-rule p-3">
        <p>{signal.text}</p>
        {x.last_grade_change?.drivers.some(d => d.factor === signal.factor && d.before === d.after && d.after === signal.points)
          && <p className="text-xs text-muted">Contribution at the grade change · {signal.points} points, unchanged</p>}
        {signal.sources.map(({ evidence, origin, published, title }, i) => <p key={i} className="mt-1 text-xs text-muted">
          {origin} · {published ?? "Publication date unknown"} · {/^https?:\/\//i.test(evidence.path)
            ? <a className="underline" href={evidence.path} target="_blank" rel="noreferrer">Source ↗</a>
            : title || evidence.path.split("/").pop()}
        </p>)}
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
