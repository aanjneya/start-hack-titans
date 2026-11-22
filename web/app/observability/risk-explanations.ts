import type { Evidence, ExtendedRiskReason, RiskScore } from "@/lib/api";

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
  external_politics: "Political or trade signals flag a possible disruption in the supplier’s region.",
  shift_slack: "The remaining packing work leaves little time to recover from a delay.",
  baseline: "The rating found no material disruption.",
};

/** Translate the recorded reason into one sentence for the buyer. */
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
  if (reason.factor === "external_politics" && text.includes("Polymarket")) return "Market prices flag possible disruption to this supplier’s trade routes.";
  if (reason.factor === "quality_hold_failed_aql") {
    const lot = text.match(/lot (\S+) \(([\d,]+) pcs\) failed/i);
    if (lot) return `Lot ${lot[1]} failed inspection, affecting ${lot[2]} units.`;
  }
  if (reason.factor === "ecn_revision_block") {
    const rev = text.match(/rev (\S+) blocked.*?: ([\d,]+) stock/i);
    if (rev) return `An engineering change blocks ${rev[2]} units of revision ${rev[1]} in stock.`;
  }
  if (reason.factor === "telemetry" && /^The last update /.test(text)) return text;
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

type Observation = NonNullable<RiskScore["reasons"][number]["signal_inputs"]>[string][number];
export type RatingCitation = {
  label: string; mode: string; detail: string; path: string; href?: string; findingId?: number;
};

function localSourceName(path: string, names: RiskNames) {
  const [file] = path.split("#");
  const basename = file.split("/").pop() || file;
  const supplier = file.match(/(?:^|\/)(SUP-\d+)_([^/]+)/);
  const owner = supplier ? names.supplier[supplier[1]] || supplier[2].replaceAll("_", " ") : "";
  const stem = basename.replace(/\.[^.]+$/, "").replaceAll("_", " ").trim();
  if (owner && /^(profile|components)( delta)?$/i.test(stem)) return `${owner} · ${stem === "profile" ? "supplier profile" : "component register"}`;
  return stem ? stem[0].toUpperCase() + stem.slice(1) : "Customer record";
}

const publishers: Record<string, string> = {
  "polymarket.com": "Polymarket", "open-meteo.com": "Open-Meteo", "news.google.com": "Google News",
  "federalregister.gov": "US Federal Register", "eur-lex.europa.eu": "EUR-Lex", "wto.org": "World Trade Organization",
  "mofcom.gov.cn": "China’s Ministry of Commerce",
};

function citation(evidence: Evidence, names: RiskNames, reason: string, observation?: Observation): RatingCitation {
  const path = evidence.path || "";
  const finding = path.match(/^issue:\/\/(\d+)$/);
  if (finding) return { label: `Finding #${finding[1]}`, mode: "", detail: "", path, findingId: Number(finding[1]) };
  let domain = "";
  try {
    const url = new URL(path);
    if (["https:", "http:"].includes(url.protocol)) domain = url.hostname.toLowerCase();
  } catch { /* Customer files use dataset-relative paths. */ }
  const replay = observation?.live === false || evidence.source_kind === "Offline example" || /\boffline\b/i.test(evidence.quote || "");
  const provider = observation?.provider || evidence.locator?.split(":")[0];
  const site = Object.keys(publishers).find(host => domain === host || domain.endsWith("." + host));
  const label = domain ? (site ? publishers[site] : domain) : localSourceName(path, names);
  const mode = replay ? "Offline example" : observation?.last_known_at ? `Last known reading · ${observation.last_known_at.slice(0, 10)}`
    : provider === "portal_news" ? "Customer news record" : provider === "telemetry" ? "Shipment record"
    : observation?.live && domain ? "Public feed at collection" : "";
  const result = { label, mode, detail: "", path, href: domain && !replay ? path : undefined };
  if (provider === "polymarket" || site === "polymarket.com") {
    const recorded = observation?.market?.yes_pct;
    const legacy = evidence.quote?.match(/\bYes\s*=\s*(\d+(?:\.\d+)?)\s*%/i)?.[1];
    const yes = recorded ?? (legacy ? Number(legacy) : undefined);
    const valid = typeof yes === "number" && Number.isFinite(yes) && yes >= 0 && yes <= 100;
    const price = valid ? `Yes ${yes}%` + (observation?.market?.disruption_outcome === "No"
      ? ` · disruption side No ${Number((100 - yes).toFixed(2))}%` : "") : "Price unavailable";
    const question = observation?.title || reason.match(/Polymarket odds [\d.]+%:\s*([^?]+\?)/i)?.[1];
    const event = site === "polymarket.com" && path.match(/\/event\/([\w-]+)/i)?.[1]?.replaceAll("-", " ");
    const context = question || (event ? `Event · ${event}` : "Market question unavailable");
    return { ...result, label: "Polymarket", detail: `${context} · ${price}` };
  }
  const notice = evidence.locator === "notice" ? evidence.quote?.split("\n").find(line => /notice|licen[cs]|suspension/i.test(line)) : "";
  const locator = notice || (evidence.locator && !evidence.locator.includes(":") ? evidence.locator : "");
  const detail = observation?.title || locator || (domain ? "" : evidence.quote?.split("\n")[0]?.slice(0, 140)) || "";
  return { ...result, detail };
}

export function reasonsForRating(row: RiskScore, names: RiskNames) {
  const inputs = row.reasons.find(r => r.signal_inputs)?.signal_inputs ?? {};
  return (row.reasons as ExtendedRiskReason[]).filter(r => r.text).map(reason => {
    const headline = reasonHeadline(reason, names);
    // The Rater retains precisely the observations used for each factor, including both inputs to market risk.
    // Product and component rows instead carry the evidence inherited from the contributing component or supplier.
    const observations = inputs[reason.factor || ""] ?? [];
    const citations = observations.length ? observations.map(o => citation(o.evidence, names, reason.text, o))
      : (reason.evidence ?? []).filter(e => e.path).map(e => citation(e, names, reason.text));
    const preferred = /export licens/i.test(headline) ? citations.find(s => /licen[cs]|notice/i.test(s.path)) : null;
    const sources = [...new Map((preferred ? [preferred, ...citations] : citations).map(s =>
      [JSON.stringify([s.path, s.detail, s.mode]), s])).values()];
    return { headline, sources };
  });
}
