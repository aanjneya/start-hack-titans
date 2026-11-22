import type { ExtendedRiskReason, RiskScore } from "@/lib/api";

export type RiskNames = Record<RiskScore["entity_type"], Record<string, string>>;

// These are the inclusive lower bounds in risk_scoring.score_to_grade. Scores can be fractional.
export const riskBands = [
  { rating: "AAA", minimum: 0, label: "Lowest" },
  { rating: "AA", minimum: 20, label: "Low" },
  { rating: "A", minimum: 35, label: "Moderate" },
  { rating: "BBB", minimum: 50, label: "Elevated" },
  { rating: "B", minimum: 65, label: "High" },
  { rating: "CCC", minimum: 80, label: "Critical" },
] as const;

export function recordedScore(row: RiskScore): number | undefined {
  const value = (row.reasons?.[0] as ExtendedRiskReason | undefined)?.score_0_100;
  return typeof value === "number" && Number.isFinite(value) ? value : undefined;
}

function supplierConcern(reason: ExtendedRiskReason, supplier: string): string | undefined {
  const text = reason.text;
  if (text.includes("conditional quality release expired")) return `Quality approval has expired at ${supplier}.`;
  if (text.includes("suspension/export-licence notice")) {
    if (reason.evidence?.some(source => /export licen[cs]/i.test(source.quote))) return `${supplier} flagged an export licensing issue.`;
    return `${supplier} issued a notice about supply or export licences.`;
  }
  if (text.includes("planned equipment downtime")) return `${supplier} has scheduled equipment downtime.`;
  if (text.includes("short-shipment offload")) return `Part of ${supplier}’s air shipment was left behind.`;
  if (text.includes("supplier status 'watch'")) return `${supplier} is on the purchasing team’s watchlist.`;
  return undefined;
}

function stockConcern(text: string, names: RiskNames): string {
  const short = text.match(/^Stock shortfall: ([\d,]+) (\S+) copies at risk/);
  if (short) return `Current stock alone leaves ${short[1]} copies of ${names.product[short[2]] || short[2]} uncovered.`;
  if (text.startsWith("Zero warehouse stock")) return "The warehouse has no usable stock.";
  const hours = text.match(/([\d,.]+)h (?:buffer|stock|usable stock)/)?.[1];
  return hours ? `Stock covers ${hours} packing hours at the recorded rate.` : "Stock may run short before packing finishes.";
}

const factorSummaries: Record<string, string> = {
  ecn_revision_block: "An engineering change prevents use of the older version.",
  short_shipment: "Some of the ordered units will miss the launch.",
  quality_hold_failed_aql: "A batch failed inspection and needs a quality decision.",
  shipment_late_for_launch: "The shipment is due after the packing deadline.",
  tracker_mismatch: "The carrier and purchasing records disagree about the shipment.",
  po_stuck_approval: "A purchase order is waiting for approval.",
  single_source_export_risk: "A possible export restriction needs checking with the only supplier.",
  export_ban: "Export restrictions threaten future deliveries.",
  regulatory_change: "A regulatory update needs checking against this supplier’s orders.",
  weather: "Weather on the shipping route could disrupt deliveries.",
  telemetry: "A shipment tracking update needs checking.",
  external_politics: "Prediction markets flag a possible disruption in the supplier’s region.",
  shift_slack: "The remaining packing work leaves little time to recover from a delay.",
  baseline: "The rating found no material disruption.",
};

/** Give the buyer one sentence. Keep the original statement and citations in the disclosure. */
export function reasonHeadline(reason: ExtendedRiskReason, names: RiskNames): string {
  let text = reason.text;
  let part = "";
  // Product drivers contain the specific part and its recorded reason, even on historical drops.
  if (reason.factor === "bottleneck_part") {
    const bottleneck = text.match(/^Bottleneck (\S+) rated \S+ \(\d+\/6\): (.+)$/);
    if (!bottleneck) return "A component raises this product’s supply risk.";
    part = (names.part[bottleneck[1]] || bottleneck[1]).replace(/\s+\([^()]+\)$/, "");
    text = bottleneck[2];
  }
  const about = (line: string) => part ? `${part} — ${line}` : line;
  const supplier = text.match(/^Supplier exposure: (.+?) \([^()]+\) rated /)?.[1];
  if (supplier) {
    const detail = supplierConcern(reason, supplier);
    if (detail) return about(detail);
    if (text.includes("Trade/export news")) return about(`Trade news points to a possible disruption for ${supplier}.`);
    if (text.includes("Polymarket")) return about(`A prediction market flags a possible disruption in ${supplier}’s region.`);
    return about(`${supplier}’s risk rating raises the risk for this part.`);
  }
  if (reason.factor === "stock_buffer" || /^(Stock shortfall|Zero warehouse stock|Tight line-stop buffer|Low launch buffer):? /.test(text)) {
    return about(stockConcern(text, names));
  }
  if (reason.factor === "supplier_health") {
    return supplierConcern(reason, text.split(": ")[0]) || "The supplier’s purchasing status needs review.";
  }
  if (reason.factor === "single_source" || text.startsWith("Single-source dependency:")) {
    const only = text.match(/100% supplied by (.+) \([^)]+\)$/)?.[1];
    return about(only ? `${only} is the only recorded supplier.` : "There is only one recorded supplier.");
  }
  if (reason.factor === "internal_politics") {
    const orders = text.match(/(\d+) PO\(s\) stuck/)?.[1];
    if (orders) return `${orders} purchase ${orders === "1" ? "order needs" : "orders need"} internal approval.`;
    return text.includes("seat") ? "A category lead position is vacant." : "The purchasing team has unresolved high-priority issues.";
  }
  if (reason.factor === "trade_news") return text.replace(/^Trade\/export news \([^)]+\):\s*/, "");
  if (reason.factor === "lead_time_spike") {
    const days = text.match(/to (\d+)d at/)?.[1];
    return days ? `The supplier now needs ${days} days to deliver.` : "The supplier needs more time to deliver.";
  }
  if (reason.factor === "transport") {
    return text.includes("delayed") ? "A shipment has been delayed." : "The supplier relies on sea freight.";
  }
  if (reason.factor === "bottleneck_part") return about("Supply of this component raises the product’s risk.");
  return factorSummaries[reason.factor ?? ""] ?? "A supply issue contributed to the rating.";
}
