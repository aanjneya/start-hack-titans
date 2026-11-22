import Link from "next/link";
import type { ExtendedRiskReason, RiskScore } from "@/lib/api";
import { findingHref } from "../buyer-links";
import { reasonsForRating, type RatingCitation, type RiskNames } from "./risk-explanations";

type Props = { row: RiskScore; names: RiskNames };
const factorNames: Record<string, string> = {
  worst_bom_part: "Highest component score", high_risk_bom_density: "Other exposed parts", shift_slack: "Time left to pack",
  supplier_risk: "Supplier risk", single_source: "Reliance on one supplier", stock_buffer: "Stock coverage", part_shocks: "Recorded disruptions",
  supplier_health: "Supplier status", internal_politics: "Purchasing issues", transport: "Transport", trade_news: "Trade news",
  external_politics: "Markets and political news", weather: "Weather", telemetry: "Shipment tracking", regulatory_change: "Regulatory changes",
};

/** The directory row is already a link. Keep its evidence legible without nesting another link. */
export function RatingReasonSummary({ row, names }: Props) {
  const reasons = reasonsForRating(row, names);
  const first = reasons[0];
  if (!first) return <span className="block text-xs text-muted">No reason recorded.</span>;
  const source = first.sources[0];
  const market = reasons.flatMap(r => r.sources).find(s => s.label === "Polymarket");
  return <span className="mt-1 block break-words text-sm leading-snug text-ink">
    {first.headline}
    {source && source !== market && <span className="mt-0.5 block text-xs text-muted">
      {source.label}{source.mode && ` · ${source.mode}`}
    </span>}
    {market && <span className="mt-1 block text-xs text-muted">
      <strong className="font-semibold text-ink">{market.label}</strong>{market.mode && ` · ${market.mode}`} · {market.detail}
    </span>}
  </span>;
}

function Citation({ source, headline }: { source: RatingCitation; headline: string }) {
  const label = source.findingId != null
    ? <Link href={findingHref(source.findingId)} className="font-semibold text-act underline">{source.label}</Link>
    : source.href
      ? <a href={source.href} target="_blank" rel="noreferrer" className="font-semibold text-act underline">{source.label} ↗</a>
      : <span title={source.path} className="font-semibold">{source.label}</span>;
  return <span className="block break-words text-xs leading-relaxed text-muted">
    {label}{source.mode && ` · ${source.mode}`}{source.detail && source.detail !== headline && ` · ${source.detail}`}
  </span>;
}

export function RatingReasons({ row, names }: Props) {
  const reasons = reasonsForRating(row, names);
  const metadata = row.reasons[0] as (ExtendedRiskReason & {score_basis?: {internal: number; external: number; external_raw: number; external_cap: number}}) | undefined;
  const breakdown = Object.entries(metadata?.breakdown ?? {}).filter(([, points]) => points > 0);
  const basis = metadata?.score_basis;
  return <section className="text-sm">
    <h4 className="font-semibold text-ink">What drives this rating?</h4>
    {reasons.length ? <ul className="mt-1 divide-y divide-rule">
      {reasons.map(({headline, sources}, i) => <li key={i} className="py-2">
        <p className="break-words font-medium leading-snug text-ink">{headline}</p>
        <div className="mt-1">
          {sources.length ? sources.map(source => <Citation key={source.path + source.detail} source={source} headline={headline} />)
            : <span className="text-xs text-muted">No source recorded.</span>}
        </div>
      </li>)}
    </ul> : <p className="my-2 text-muted">No reasons were recorded.</p>}
    {breakdown.length > 0 && <div className="mt-1 border-t border-rule pt-2 text-xs text-muted">
      <h5 className="font-semibold text-ink">Score calculation</h5>
      <p className="mt-1">{row.entity_type === "product" ? "The highest-risk component sets the starting score. Other exposed parts and tight packing time can raise it."
        : row.entity_type === "part" ? "The rating combines supplier risk, stock coverage, sourcing alternatives and known disruptions."
        : basis ? `${basis.internal} points from internal records + ${basis.external} from external signals.${basis.external_raw > basis.external ? ` The combined external contribution is capped at ${basis.external_cap} points.` : ""}`
        : "The rating combines the supplier’s internal purchasing records and external signals."}</p>
      <dl className="mt-2 flex flex-wrap gap-x-3 gap-y-1 tabular">
        {breakdown.map(([key, points]) => <div key={key} className="inline-flex min-w-0 items-baseline gap-1">
          <dt>{factorNames[key] || key.replaceAll("_", " ")}</dt><dd className="font-semibold">{points}</dd>
        </div>)}
      </dl>
    </div>}
  </section>;
}
