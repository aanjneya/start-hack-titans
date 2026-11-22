import type { CustomerSetup, DecisionOption } from "../../API_CONTRACT";

export const formatMoney = (unit = "EUR") => new Intl.NumberFormat("en-IE", {
  style: "currency", currency: unit, minimumFractionDigits: 0, maximumFractionDigits: 2,
});

const euro = formatMoney();

export function CompanyRules({ setup }: { setup: CustomerSetup }) {
  return <section id="company-rules" aria-label="Company approval rules"
    className="grid basis-full gap-4 rounded-lg border border-rule bg-surface p-4 text-sm sm:grid-cols-2">
    <div><h2 className="font-bold">{setup.customer} approval rules</h2>
      <p className="mt-2">{setup.autonomy.mode === "control"
        ? "Control mode sends each proposed action to its assigned signer."
        : `Auto mode can execute eligible actions below ${euro.format(setup.autonomy.auto_execute_below_eur)}. Other actions wait for the assigned signer.`}</p>
      <p className="mt-2 text-muted">The PO value determines the approval band. The customer’s org chart identifies the person for that role and category.</p>
    </div>
    <table className="w-full text-left tabular"><thead><tr className="text-muted"><th>PO approval value</th><th>Required role</th></tr></thead>
      <tbody>{setup.approval_matrix.map((r,i,rows) => {
        const lower = rows[i-1]?.below_eur ?? 0;
        const label = r.below_eur == null ? `${euro.format(lower)} and above` :
          lower ? `${euro.format(lower)} to below ${euro.format(r.below_eur)}` : `Below ${euro.format(r.below_eur)}`;
        return <tr key={i} className="border-t border-rule"><td className="py-1 pr-2">{label}</td><td>{r.approvers.join(" + ")}</td></tr>;
      })}</tbody></table>
  </section>;
}

export function OrderCalculation({ basis }: { basis: NonNullable<DecisionOption["approval_basis"]> }) {
  return <details className="mt-3">
    <summary className="cursor-pointer font-semibold text-act">How the amended PO adds up</summary>
    <p className="mt-2 tabular">PO {basis.po} · {euro.format(basis.order_eur)} + {euro.format(basis.fee_eur)} freight
      {" = "}<strong>{euro.format(basis.total_eur)}</strong></p>
    <ul className="mt-1 divide-y divide-rule">{basis.lines.map(l => <li className="py-1 text-muted" key={l.line}>
      Line {l.line} · {l.material} · {l.quantity.toLocaleString("en-GB")} units · {euro.format(l.value_eur)}
    </li>)}</ul>
  </details>;
}
