import type { SupplyExposure } from "../../API_CONTRACT";

const fmt = (value: number) => value.toLocaleString("en-GB");

const factorNames: Record<string, string> = {
  trade_news: "Trade news", external_politics: "Market odds", regulatory_change: "Regulatory watch",
  telemetry: "Shipment telemetry", weather: "Port weather", supplier_health: "Supplier status and notices",
  internal_politics: "Internal approvals and escalations", transport: "Transport",
};

export function ExposureSummary({ exposure }: { exposure: SupplyExposure }) {
  const { orders, previous_lead_days: before, lead_days: after, assembly } = exposure;
  const change = before != null && after != null ? after - before : null;
  const knownUnits = orders.length > 0 && orders.every(o => o.quantity != null);
  return <div className="mt-3">
    {exposure.supplier_rating && <p className="mb-2 text-sm font-semibold text-warn">
      {exposure.supplier} · Supplier grade {exposure.last_grade_change && `${exposure.last_grade_change.previous} → `}{exposure.supplier_rating}
    </p>}
    <p className="font-medium">{orders.length > 0
      ? `${orders.length} open order ${orders.length === 1 ? "line falls" : "lines fall"} inside the supplier’s delivery window.`
      : "The affected order lines still need to be established."}
      {knownUnits && ` Together they represent ${fmt(orders.reduce((sum, o) => sum + o.quantity!, 0))} ordered units.`}</p>
    <p className="mt-1 text-sm text-muted">
      {change != null && change > 0 ? `Lead time grew by ${change} days, from ${before} to ${after}. ` :
        after != null ? `Recorded lead time is ${after} days. ` : ""}
      {assembly ? "Existing assemblies already contain this part. Opening a review adds no copies to the current forecast." :
        "Verify the supplier’s exposure before committing spend."}
    </p>
  </div>;
}

export function ExposureEvidence({ exposure: x }: { exposure: SupplyExposure }) {
  const changed = x.last_grade_change?.drivers.filter(d => d.after !== d.before)
    .sort((a, b) => Math.abs(b.after - b.before) - Math.abs(a.after - a.before));
  return <div className="mt-4 space-y-4">
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
        {x.assembly && ` The supplier fits it into ${x.assembly}.`}</p>
      <p className="text-muted">{x.effective_after ? `Supplier notice covers deliveries after ${x.effective_after}.` : "Effective date unknown."}</p>
      {x.uncertainties?.map(note => <p key={note} className="mt-2 font-semibold text-warn">{note}</p>)}
      {x.orders.length > 0 && <table className="mt-2 w-full text-left text-sm tabular">
        <thead className="text-muted"><tr><th>PO / line</th><th>Promised</th><th className="text-right">Units</th></tr></thead>
        <tbody>{x.orders.map(o => <tr className="border-t border-rule" key={o.po + "/" + o.line}>
          <td>{o.po}/{o.line ?? "?"}</td><td>{o.promised}</td><td className="text-right">{o.quantity == null ? "Unknown" : fmt(o.quantity)}</td>
        </tr>)}</tbody>
      </table>}
      {x.order_value && <p className="mt-2 font-semibold">Recorded value of these PO lines · {x.order_value.currency} {fmt(x.order_value.amount)}</p>}
    </section>
    {!!x.sample_offers?.length && <section className="rounded border-l-4 border-act bg-act-soft px-3 py-2">
      <h4 className="font-bold">An alternative to investigate</h4>
      {x.sample_offers.map(offer => <blockquote className="mt-1" key={offer.source + offer.text}>{offer.text}
        <cite className="block text-xs text-muted">{offer.source.split("/").pop()}</cite>
      </blockquote>)}
      <p className="mt-2">Ask Engineering to check fit and Quality to set release criteria before placing an order.</p>
    </section>}
  </div>;
}
