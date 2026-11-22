"use client";

import { ChevronDown, ExternalLink } from "lucide-react";
import type { RiskScore } from "@/lib/api";

// ---------------------------------------------------------------------------
// Plain-language risk cards for the Risk Rater report. Each card says what could go wrong, then the top two reasons
// with the source behind each one: first the strongest driver from Soony's own data (the reason that scores most), then
// the strongest outside feed (Polymarket, Google News, satellite shipment tracking, weather). A part is tied to the
// export news only when the Risk Rater itself links it (export_ban / single_source_export_risk). Nothing is invented:
// every line comes from the Risk Rater's reasons, evidence and signal inputs.
// ---------------------------------------------------------------------------

type Evidence = { path?: string; locator?: string; quote?: string };
type Signal = {
  provider?: string; title?: string; summary?: string; live?: boolean; published?: string | null;
  evidence?: Evidence; market?: { yes_pct?: number } | null; shipment_ids?: string[];
};
type Reason = {
  text?: string; factor?: string; points?: number; evidence?: Evidence[];
  recommended_action?: { label?: string } | null; signal_inputs?: Record<string, Signal[]>;
  breakdown?: Record<string, number>;
};

export type Source = {
  key: string;
  /** Who it comes from, in plain words: "Polymarket", "Soony portal news", "Supply notice". */
  from: string;
  /** What it says, one line, no file names. */
  title: string;
  /** Only for live outside feeds: offline snapshots have no real page to open. */
  href?: string;
  /** Soony portal data, our own data check, a live outside feed, or a fixed offline snapshot (demo mode). */
  kind: "soony" | "check" | "live" | "example";
};

export type PlainRisk = {
  key: string;
  type: "supplier" | "part" | "product";
  id: string;
  name: string;
  rating: string;
  grade: number;
  risk: string;
  /** The top two reasons in plain words, each with the source behind it. */
  reasons: { text: string; source?: Source }[];
  sources: Source[];
  next?: string;
};

const PROVIDER: Record<string, string> = {
  portal_news: "Soony portal news",
  gnews: "Google News",
  polymarket: "Polymarket",
  weather: "Weather (Open-Meteo)",
  telemetry: "Satellite tracking",
  regulations: "Regulation watch",
};
/** Reasons that only come from outside feeds (offline snapshots in demo mode): never the lead reason if Soony data explains it. */
const OFFLINE_FACTORS = new Set(["external_politics", "regulatory_change"]);
/** The Risk Rater's own link from a part to the export-licence story. */
const EXPORT_LINK = new Set(["export_ban", "single_source_export_risk"]);

const isUrl = (p?: string) => !!p && /^https?:\/\//i.test(p);
const lowerFirst = (s: string) => (s && !/^[A-Z]{2}/.test(s) ? s.charAt(0).toLowerCase() + s.slice(1) : s);
const upperFirst = (s: string) => (s ? s.charAt(0).toUpperCase() + s.slice(1) : s);
const num = (v: string) => Number(v.replace(/,/g, ""));
const fmtN = (v: number) => v.toLocaleString("en-US", { maximumFractionDigits: 1 });
const niceDate = (iso: string) => {
  const d = new Date(`${iso}T12:00:00Z`);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleDateString("en-GB", { day: "numeric", month: "short", timeZone: "UTC" });
};
/** "Will major … Suez? (KR exposure)" → "Will major … Suez?" */
const question = (q: string) => q.replace(/\s*\([A-Z]{2} exposure\)\s*$/, "").trim();
const COUNTRY: Record<string, string> = { CN: "China", KR: "Korea", IN: "India", PL: "Poland", US: "the US", DE: "Germany", JP: "Japan" };
const escapeRe = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
const humanIds = (text: string, known: Record<string, string>) =>
  text.replace(/CMP-[A-Z]+-\d+|SUP-\d+/g, (id) => known[id] ?? id)
    .replace(/\bnon-([A-Z]{2})\b/g, (_, c: string) => `non-${COUNTRY[c] ?? c}`)
    .replace(/\b(CN|KR|IN|PL)\b/g, (c) => COUNTRY[c] ?? c)
    .replace(/\b2nd source\b/gi, "second supplier");

/** "status=at risk, share=1.0, lead_time=35d" → "status at risk, share 100%, lead time 35 days" (empty fields dropped). */
function kvWords(quote: string): string {
  if (!/\w+=/.test(quote)) return quote;
  return quote.split(/,\s*(?=\w+=)/).map((pair) => {
    const [k, ...rest] = pair.split("=");
    const v = rest.join("=").trim().replace(/^'|'$/g, "");
    if (!v) return "";
    const key = k.trim().replace(/_/g, " ");
    if (k.trim() === "share" && !Number.isNaN(Number(v))) return `share ${Math.round(Number(v) * 100)}%`;
    if (/^\d+d$/.test(v)) return `${key} ${v.slice(0, -1)} days`;
    if (/^-?\d+(\.\d+)?$/.test(v)) return `${key} ${fmtN(Number(v))}`;
    return `${key} ${v.replace(/_/g, " ")}`;
  }).filter(Boolean).join(", ");
}

function supplierFromPath(path: string, known: Record<string, string>): string {
  const m = path.match(/suppliers\/(SUP-\d+)_([^/]+)/);
  return m ? known[m[1]] ?? m[2].replace(/_/g, " ") : "";
}

function fileSource(e: Evidence, known: Record<string, string>, context = ""): Source | null {
  const path = e.path ?? "";
  if (!path) return null;
  const base = path.split("#")[0];
  const lines = (e.quote ?? "").split("\n").map((l) => l.trim()).filter(Boolean);
  const firstLine = lines[0] ?? "";
  if (isUrl(path)) {
    const host = new URL(path).hostname.replace(/^www\./, "");
    const offline = /offline/i.test(e.quote ?? "");
    const from = /polymarket/.test(host) ? "Polymarket" : /open-meteo/.test(host) ? "Weather (Open-Meteo)"
      : /news\.google/.test(host) ? "Google News" : "Regulation watch";
    const odds = context.match(/odds (\d+)%:\s*(.+)/);
    const title = odds ? `${odds[1]}% say yes: ${question(odds[2])}`
      : (e.quote ?? "").replace(/\s*\((offline|live)[^)]*\)\s*/gi, "").trim() || from;
    return {
      key: `${from}|${odds ? question(odds[2]) : title}`, from, title,
      href: !offline && !/^api\./.test(host) ? path : undefined, kind: offline ? "example" : "live",
    };
  }
  if (path.startsWith("issue://")) {
    return { key: `check|${firstLine.slice(0, 60)}`, from: "Our data check", title: firstLine || "Reconciler finding", kind: "check" };
  }
  const sup = supplierFromPath(path, known);
  const file = base.split("/").pop() ?? "";
  const words = kvWords(lines.join(", "));
  let from = "Soony data";
  let title = words;
  if (/\/notices\//.test(path)) {
    from = "Supply notice";
    const what = /export_?licen/i.test(file) ? "rare-earth export licence" : /przestoj|downtime/i.test(file) ? "planned equipment downtime" : "supply notice";
    title = sup ? `${sup}: ${what}` : what;
  } else if (/profile\.csv$/.test(path)) {
    from = "Supplier profile"; title = sup ? `${sup}: ${words}` : words;
  } else if (/components\.csv$/.test(path)) {
    from = "Supplier part list"; title = sup ? `${sup}: ${words}` : words;
  } else if (/Shipping_advice/i.test(file)) {
    from = "Shipping advice"; title = sup || "supplier shipping advice";
  } else if (/^OA\d+/i.test(file)) {
    from = "Order confirmation"; title = sup || "order confirmation";
  } else if (/soony\/news\//.test(path)) {
    from = "Soony portal news"; title = firstLine.split(/(?<=\.)\s/)[0] || "news article";
  } else if (/stock_snapshot/.test(file)) {
    from = "Stock list";
  } else if (/open_shipments_tracker/.test(file)) {
    from = "Shipment tracker";
  } else if (/open_shipments/.test(file)) {
    from = "Shipment list";
  } else if (/purchase_orders/.test(file)) {
    from = "Purchase orders";
  } else if (/inspection_report/.test(file)) {
    from = "Inspection report"; title = words || "goods-in inspection";
  } else if (/bom_delta/.test(file)) {
    from = "Bill of materials change";
  } else if (/org_chart/.test(file)) {
    from = "Org chart";
  }
  const shp = `${firstLine} ${context}`.match(/SHP-\d{4}-\d+/)?.[0] ?? "";
  const key = /soony\/news\//.test(path) ? `news|${title.slice(0, 40)}` : `${base}|${shp || e.locator || firstLine.slice(0, 40)}`;
  return { key, from, title: title || from, kind: "soony" };
}

function signalSource(s: Signal): Source {
  const path = s.evidence?.path;
  const portal = !!path && !isUrl(path); // portal news and shipment records are real Soony files
  const offline = /offline/i.test(s.evidence?.quote ?? "") || s.live !== true;
  const kind: Source["kind"] = portal ? "soony" : offline ? "example" : "live";
  const from = PROVIDER[s.provider ?? ""] ?? upperFirst(s.provider ?? "Outside feed");
  const title = s.provider === "polymarket" && s.market?.yes_pct != null
    ? `${Math.round(s.market.yes_pct)}% say yes: ${question(s.title ?? "")}`
    : s.provider === "telemetry" || s.provider === "weather" ? s.summary || s.title || "" : s.title || s.summary || "";
  const shp = s.shipment_ids?.[0] ?? `${s.title} ${s.summary}`.match(/SHP-\d{4}-\d+/)?.[0];
  const key = s.provider === "portal_news" || s.provider === "gnews" ? `news|${(s.title ?? "").slice(0, 40)}`
    : s.provider === "telemetry" && shp ? `${(path ?? "").split("#")[0]}|${shp}`
      : `${from}|${s.provider === "polymarket" ? question(s.title ?? "") : title}`;
  const host = path && isUrl(path) ? new URL(path).hostname : "";
  return { key, from, title, href: kind === "live" && path && !/^api\./.test(host) ? path : undefined, kind };
}

function signalsOf(row: RiskScore): Signal[] {
  const out: Signal[] = [];
  for (const r of (row.reasons ?? []) as Reason[]) for (const list of Object.values(r.signal_inputs ?? {})) out.push(...(list ?? []));
  return out;
}

/** One reason as a plain clause (lower-case start unless it starts with a name), no file names, ids or scores. */
function plainReason(r: Reason, known: Record<string, string>): string {
  const t = (r.text ?? "").replace(/\s*\([^)]*\.(pdf|csv|xlsx|json|xml|jpg|png)\)/gi, "").trim();
  const name = (id: string) => known[id] ?? id;
  let m: RegExpMatchArray | null;
  switch (r.factor) {
    case "trade_news": return t.replace(/^Trade\/export news \([A-Z]{2}\):\s*/, "");
    case "regulatory_change": return `new rule: ${t.replace(/^Regulatory watch:\s*/, "")}`;
    case "external_politics":
      return (m = t.match(/Polymarket odds (\d+)%:\s*(.+)/)) ? `Polymarket gives ${m[1]}% to "${question(m[2])}"` : t;
    case "supplier_risk":
      return (m = t.match(/Supplier exposure:\s*(.+?)\s*\((SUP-\d+)\)\s*rated\s*([A-Z]+)/)) ? `its supplier ${m[1]} is rated ${m[3]}` : t;
    case "single_source":
      return (m = t.match(/supplied by\s*(.+?)\s*\(/)) ? `${m[1]} is its only supplier` : "it has only one supplier";
    case "single_source_export_risk":
      return (m = t.match(/lead-time jump \((\d+)d\)/)) ? `its only supplier's lead time jumps to ${m[1]} days after the export-licence notice` : "its only supplier faces an export-licence delay";
    case "export_ban": return "export restrictions threaten its supply";
    case "telemetry":
      return (m = t.match(/update for (SHP-\d{4}-\d+) trails .*? by (\d+) hours/)) ? `satellite tracking: shipment ${m[1]} has not reported for ${m[2]} hours` : t;
    case "stock_buffer":
      return (m = t.match(/([\d.]+)h usable stock/)) ? `only about ${Math.round(Number(m[1]))} hours of usable stock left` : t;
    case "tracker_mismatch":
      return (m = t.match(/(SHP-\d{4}-\d+)/)) ? `the ERP and the shipment tracker disagree on where shipment ${m[1]} is` : t;
    case "quality_hold_failed_aql":
      return (m = t.match(/\(([\d,]+) pcs\) failed AQL/)) ? `a lot of ${fmtN(num(m[1]))} pieces failed inspection but is not on hold` : t;
    case "shipment_late_for_launch":
      return (m = t.match(/ETA (\d{4}-\d{2}-\d{2})/)) ? `no delivery arrives before launch (the earliest lands ${niceDate(m[1])})` : t;
    case "short_shipment":
      return (m = t.match(/([\d,]+) pcs offloaded past launch/)) ? `${fmtN(num(m[1]))} pieces of an order were pushed past launch` : t;
    case "ecn_revision_block":
      return (m = t.match(/rev ([A-Z]) blocked.*?:\s*([\d,]+) stock \+ ([\d,]+) inbound unusable/))
        ? `revision ${m[1]} is blocked by an engineering change: ${fmtN(num(m[2]))} in stock and ${fmtN(num(m[3]))} on the way can't be used` : t;
    case "bottleneck_part":
      return (m = t.match(/Bottleneck\s+(CMP-[A-Z]+-\d+)\s+rated\s+([A-Z]+)/)) ? `its part ${name(m[1])} is rated ${m[2]}` : t;
    case "supplier_health": {
      const body = t.includes(":") ? t.slice(t.indexOf(":") + 1) : t;
      return body.split(";").map((p) => p.trim()).filter(Boolean).map((p) => {
        const st = p.match(/supplier status '([^']+)'/);
        if (st) return `it is marked '${st[1]}'`;
        if (/export.?licen/i.test(p)) return "it sent an export-licence notice";
        if (/downtime/i.test(p)) return "it plans equipment downtime";
        const q = p.match(/quality release expired (\d{4}-\d{2}-\d{2})/);
        if (q) return `its quality release expired on ${niceDate(q[1])}`;
        return lowerFirst(p);
      }).join(" and ");
    }
    default: {
      const first = humanIds(t.split(" — ")[0], known);
      return lowerFirst(first.includes(": ") && first.indexOf(": ") < 40 ? first.slice(first.indexOf(": ") + 2) : first);
    }
  }
}

/** One outside signal as one plain sentence; `who` names the supplier when the card is about a part or an edition. */
function signalPlain(sg: Signal, who = ""): string {
  const title = (sg.title ?? "").trim();
  const owner = who ? (who.endsWith("s") ? `${who}' ` : `${who}'s `) : "";
  switch (sg.provider) {
    case "polymarket":
      return sg.market?.yes_pct != null
        ? `${who ? `For ${who}, ` : ""}Polymarket gives ${Math.round(sg.market.yes_pct)}% to "${question(title)}"` : title;
    case "telemetry": {
      const m = (sg.summary ?? "").match(/update for (\S+) trails .*? by (\d+) hours/);
      return m ? `Satellite tracking: ${owner}shipment ${m[1]} has not reported for ${m[2]} hours` : sg.summary || title;
    }
    case "weather": {
      const text = (sg.summary ?? title).replace(/^[^:]+\(([A-Z]{5})\):\s*/, "").replace(/\s&\s/g, " and ");
      return `Weather on ${who ? owner : "the "}route: ${text}`;
    }
    case "regulations": return `New rule${who ? ` affecting ${who}` : ""}: ${title.replace(/^Regulatory watch:\s*/, "")}`;
    case "gnews":
    case "portal_news": return `News${who ? ` about ${who}` : ""}: ${title}`;
    default: return title || sg.summary || "";
  }
}

const RISK_LINE: Record<PlainRisk["type"], (name: string, grade: number) => string> = {
  supplier: (nm, g) => (g >= 6 ? `Supply from ${nm} could stop.` : g >= 5 ? `${nm} may deliver late or not at all.` : g >= 4 ? `${nm} needs watching.` : `${nm} looks stable.`),
  part: (nm, g) => (g >= 6 ? `${nm} could run out.` : g >= 5 ? `${nm} supply is at risk.` : g >= 4 ? `${nm} needs watching.` : `${nm} looks covered.`),
  product: (nm, g) => (g >= 6 ? `${nm} launch could miss copies.` : g >= 5 ? `${nm} launch is at risk.` : g >= 4 ? `Some ${nm} parts need watching.` : `${nm} looks on track.`),
};

/** Feeds for the second reason, best first: news and markets for suppliers, satellite tracking for parts, the arrival
 *  route's weather for editions. Parts not tied to the export story skip country-level markets and rules. */
const FEEDS = {
  supplier: ["gnews", "portal_news", "polymarket", "telemetry", "weather", "regulations"],
  partExport: ["gnews", "portal_news", "polymarket", "telemetry", "weather"],
  part: ["telemetry", "weather", "gnews"],
  product: ["weather", "telemetry", "gnews"],
};
const KIND_ORDER: Record<Source["kind"], number> = { soony: 0, check: 1, live: 2, example: 3 };

/** Build the plain cards. `names` maps supplier and part ids to names; `editions` maps product ids to edition names. */
export function plainRisks(rows: RiskScore[], names: Record<string, string>, editions: Record<string, string>): PlainRisk[] {
  const byKey = new Map(rows.map((r) => [`${r.entity_type}/${r.entity_id}`, r]));
  const known = { ...names };
  for (const r of rows) for (const rs of (r.reasons ?? []) as Reason[]) {
    for (const m of (rs.text ?? "").matchAll(/([A-Z][\w .&-]+?)\s*\((SUP-\d+)\)/g)) known[m[2]] ??= m[1].trim();
  }
  const sorted = (row: RiskScore) => [...((row.reasons ?? []) as Reason[])].sort((a, b) => (b.points ?? 0) - (a.points ?? 0));
  const real = (row: RiskScore) => sorted(row).filter((r) => !OFFLINE_FACTORS.has(r.factor ?? ""));
  const evidenceSources = (r: Reason | undefined) =>
    (r?.evidence ?? []).map((e) => fileSource(e, known, r?.text ?? "")).filter((s): s is Source => !!s);
  const allSources = (row: RiskScore) => [...sorted(row).flatMap((r) => evidenceSources(r)), ...signalsOf(row).map(signalSource)];
  const supplierOf = (row: RiskScore, depth = 0): RiskScore | undefined => {
    if (row.entity_type === "supplier") return row;
    if (depth > 2) return undefined;
    for (const r of sorted(row)) {
      const id = (r.text ?? "").match(row.entity_type === "part" ? /(SUP-\d+)/ : /(CMP-[A-Z]+-\d+)/)?.[1];
      const next = id && byKey.get(`${row.entity_type === "part" ? "supplier" : "part"}/${id}`);
      if (next) return row.entity_type === "part" ? next : supplierOf(next, depth + 1);
    }
    return undefined;
  };
  const headlineOf = (row: RiskScore) => signalsOf(row).find((s) => s.provider === "gnews" || s.provider === "portal_news");
  const exportLinked = (row: RiskScore) => real(row).some((r) => EXPORT_LINK.has(r.factor ?? ""));

  /** The lead reason of a row: its text, the source behind it, and a short root to quote one level up. */
  type Lead = { text: string; source?: Source; root: string; extra: Source[] };
  const leadOf = (row: RiskScore): Lead => {
    if (row.entity_type === "supplier") {
      const top = real(row)[0] ?? sorted(row)[0];
      const srcs = evidenceSources(top);
      const text = top ? plainReason(top, known) : "";
      return { text, source: srcs.find((s) => s.from === "Supply notice") ?? srcs[0], root: text, extra: [] };
    }
    if (row.entity_type === "part") {
      const reasons = real(row);
      const supplier = supplierOf(row);
      const headline = supplier && headlineOf(supplier);
      if (exportLinked(row) && supplier && headline?.title) {
        const supRisk = reasons.find((r) => r.factor === "supplier_risk");
        const here = supRisk ? plainReason(supRisk, known) : `its supplier ${known[supplier.entity_id] ?? supplier.entity_id} is affected`;
        return { text: `${here} — ${headline.title}`, source: signalSource(headline), root: headline.title, extra: [] };
      }
      // Not tied to the export story: the part's own strongest problem from Soony data, else its supplier's rating.
      const own = reasons.find((r) => r.factor !== "supplier_risk" && r.factor !== "single_source") ?? reasons[0];
      const text = own ? plainReason(own, known) : "";
      return { text, source: evidenceSources(own)[0], root: text, extra: [] };
    }
    // Edition: its weakest part and that part's own reason.
    const top = sorted(row).find((r) => r.factor === "bottleneck_part") ?? sorted(row)[0];
    const partId = (top?.text ?? "").match(/(CMP-[A-Z]+-\d+)/)?.[1];
    const part = partId ? byKey.get(`part/${partId}`) : undefined;
    if (part) {
      const inner = leadOf(part);
      const partName = known[part.entity_id] ?? part.entity_id;
      return { text: `its part ${partName} (${part.rating}) — ${inner.root}`, source: inner.source, root: inner.root, extra: allSources(part) };
    }
    const text = top ? plainReason(top, known) : "";
    return { text, source: evidenceSources(top)[0], root: text, extra: [] };
  };

  return rows.map((row) => {
    const type = (["supplier", "part", "product"].includes(String(row.entity_type)) ? row.entity_type : "part") as PlainRisk["type"];
    const name = type === "product" ? editions[row.entity_id] ?? row.entity_id : known[row.entity_id] ?? row.entity_id;
    const lead = leadOf(row);
    const reasons: PlainRisk["reasons"] = [{ text: lead.text || "no risk drivers in the latest data", source: lead.source }];

    // Second reason: the strongest outside feed behind this rating (the supplier's, for parts and editions).
    const supplier = supplierOf(row);
    const order = type === "supplier" ? FEEDS.supplier : type === "product" ? FEEDS.product : exportLinked(row) ? FEEDS.partExport : FEEDS.part;
    const feeds = supplier ? signalsOf(supplier) : [];
    const feed = order.map((pv) => feeds.find((sg) => sg.provider === pv && signalSource(sg).key !== lead.source?.key
      && !lead.text.includes(sg.title ?? "\u0000"))).find(Boolean);
    if (feed && row.grade >= 4) {
      const who = supplier && supplier !== row ? known[supplier.entity_id] ?? supplier.entity_id : "";
      reasons.push({ text: signalPlain(feed, who), source: signalSource(feed) });
    }

    // All sources: the two shown first, then Soony data, our checks, live feeds, offline snapshots; no duplicates.
    const pool = [...reasons.map((r) => r.source).filter((s): s is Source => !!s), ...allSources(row), ...lead.extra,
      ...(supplier && supplier !== row ? allSources(supplier) : [])];
    const seen = new Set<string>();
    const unique = pool.filter((s) => (seen.has(s.key) ? false : (seen.add(s.key), true)));
    const shown = new Set(reasons.map((r) => r.source?.key));
    const sources = [...unique.filter((s) => shown.has(s.key)),
      ...unique.filter((s) => !shown.has(s.key)).sort((a, b) => KIND_ORDER[a.kind] - KIND_ORDER[b.kind])];

    const next = sorted(row)[0]?.recommended_action?.label;
    return {
      key: `${type}/${row.entity_id}`, type, id: row.entity_id, name, rating: row.rating, grade: row.grade,
      risk: RISK_LINE[type](name, row.grade),
      reasons, sources,
      next: next ? humanIds(next, known).replace(new RegExp(escapeRe(name), "g"), name) : undefined,
    };
  });
}

/** Grade colours, calm to dark as the risk rises (no red): AA/AAA green, A grey, BBB light amber, B amber, CCC deep. */
export const BADGE = (g: number) => (g >= 6 ? "bg-stop text-white" : g >= 5 ? "bg-warn text-white" : g >= 4 ? "bg-warn-soft text-warn"
  : g >= 3 ? "bg-dock text-muted ring-1 ring-inset ring-rule" : "bg-ok-soft text-ok");
export const GRADE_COLOR: Record<number, string> = {
  6: "var(--color-stop)", 5: "var(--color-warn)", 4: "color-mix(in srgb, var(--color-warn) 45%, white)",
  3: "var(--color-rule)", 2: "color-mix(in srgb, var(--color-ok) 35%, white)", 1: "color-mix(in srgb, var(--color-ok) 20%, white)",
};
const KIND: Record<Source["kind"], { label: string; cls: string }> = {
  soony: { label: "Soony data", cls: "bg-ok-soft text-ok" },
  check: { label: "Our data check", cls: "bg-dock text-muted" },
  live: { label: "Live", cls: "bg-act-soft text-act" },
  example: { label: "Offline snapshot", cls: "bg-warn-soft text-warn" },
};

function SourceLine({ s, short = false }: { s: Source; short?: boolean }) {
  return (
    <span className="flex min-w-0 flex-wrap items-baseline gap-x-2 gap-y-0.5">
      <span className="font-semibold text-ink">{s.from}</span>
      <span className={`rounded px-1.5 py-px text-[11px] font-semibold ${KIND[s.kind].cls}`}>{KIND[s.kind].label}</span>
      {s.href ? (
        <a href={s.href} target="_blank" rel="noreferrer"
          className="inline-flex min-w-0 items-baseline gap-1 text-act underline decoration-dotted underline-offset-2 hover:decoration-solid">
          <span className={short ? "font-semibold" : "line-clamp-2 break-words"}>{short ? "Open" : s.title}</span>
          <ExternalLink aria-hidden className="size-3 shrink-0 translate-y-0.5" />
        </a>
      ) : (
        !short && <span className="line-clamp-2 break-words text-muted">{s.title}</span>
      )}
    </span>
  );
}

export function RiskCard({ r }: { r: PlainRisk }) {
  const calm = r.grade <= 3;
  return (
    <article className="rounded-xl border border-rule bg-surface p-4 text-sm">
      <div className="flex items-start justify-between gap-3">
        <p className="min-w-0">
          <span className="font-bold text-ink">{r.name}</span>
          {r.name !== r.id && <span className="ml-1.5 text-xs text-muted">{r.id}</span>}
        </p>
        <span className={`shrink-0 rounded px-2 py-0.5 text-xs font-bold tabular ${BADGE(r.grade)}`} title={`Grade ${r.grade} of 6`}>
          {r.rating}
        </span>
      </div>
      <div className="mt-2 rounded-lg bg-dock/70 p-3">
        <p className="font-semibold text-ink">{r.risk}</p>
        <ol className="mt-2 space-y-2">
          {r.reasons.map((reason, i) => (
            <li key={i} className="flex gap-2">
              <span aria-hidden className="mt-0.5 flex size-4 shrink-0 items-center justify-center rounded-full bg-surface text-[10px] font-bold text-muted ring-1 ring-rule">
                {i + 1}
              </span>
              <span className="min-w-0">
                <span className="block text-ink/90">
                  {i === 0 && <span className="text-muted">{calm ? "Watch point: " : "Because "}</span>}
                  {i === 0 && !calm ? reason.text : upperFirst(reason.text)}
                </span>
                {reason.source && <span className="mt-0.5 block text-xs"><SourceLine s={reason.source} short /></span>}
              </span>
            </li>
          ))}
        </ol>
      </div>
      {r.sources.length > r.reasons.filter((x) => x.source).length && (
        <details className="group mt-2 rounded-lg border border-rule">
          <summary className="flex cursor-pointer list-none items-center gap-2 px-3 py-2 text-xs font-semibold text-act [&::-webkit-details-marker]:hidden">
            All {r.sources.length} sources
            <ChevronDown aria-hidden className="size-3.5 transition-transform group-open:rotate-180" />
          </summary>
          <ul className="space-y-2 border-t border-rule px-3 py-2 text-xs">
            {r.sources.map((src) => <li key={src.key}><SourceLine s={src} /></li>)}
          </ul>
        </details>
      )}
      {r.next && r.grade >= 4 && <p className="mt-2 text-xs text-muted"><span className="font-semibold text-act">Next step</span> · {r.next}</p>}
    </article>
  );
}
