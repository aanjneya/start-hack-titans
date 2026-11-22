// The deterministic "brain" behind the workspace chat. planRun() matches a question to an intent and plans
// 2–4 steps; each step re-fetches what it needs (fresh data per question), keeps it in closure state and
// resolves to a short detail line. compose() then writes the answer from that state. Every figure comes from
// fetched data: no ids, quantities or names from the data are hardcoded here.
import {
  clock, fmt, getData, parseTs,
  type AgentLogEntry, type DecisionCard, type DropRecord, type ExtendedRiskReason, type FieldChange,
  type Forecast, type HealthCheckResponse, type Issue, type OutboxMessage, type RiskScore,
} from "@/lib/api";
import {
  TRUTH_TABLES, buildPartRows, closeDate, coverTone, dropLabel, fetchJourney, fetchTruthTable, fileFolder, fileKind,
  isOpenPoLine, loadTruth, newestForecasts, poLines, sourceFile,
  type BomRow, type JourneyRow, type MaterialRow, type PartRow, type PoLine, type ShipmentRow,
  type StockLotRow, type SupplierRow, type TruthData,
} from "./data";
import type {
  AgentAnswer, AnswerBlock, AnswerCell, BrainContext, CellTone, PlannedRun, RunStep, WorkspaceAgent,
} from "./types";

// ---------------------------------------------------------------------------
// Backend calls. A failure becomes a plain sentence on the step card.
// ---------------------------------------------------------------------------
async function load<T>(what: string, fn: () => Promise<T>): Promise<T> {
  try {
    return await fn();
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e);
    const offline = /fetch|network|load failed|refused|unable to connect/i.test(msg);
    throw new Error(offline ? `Couldn't reach the backend to load ${what}` : `Couldn't load ${what}: ${msg}`);
  }
}

const api = {
  forecast: () => load("the Launch Planner forecast", () => getData<Forecast[]>("/api/forecast")),
  proposed: () => load("the decision cards", () => getData<DecisionCard[]>("/api/decisions?status=proposed")),
  issues: () => load("the findings", () => getData<Issue[]>("/api/issues")),
  changes: () => load("the change log", () => getData<FieldChange[]>("/api/changes?limit=300")),
  drops: () => load("the portal drops", () => getData<DropRecord[]>("/api/drops")),
  risk: () => load("the risk ratings", () => getData<RiskScore[]>("/api/risk")),
  outbox: () => load("the Chaser's emails", () => getData<OutboxMessage[]>("/api/outbox")),
  health: () => load("the backend status", () => getData<HealthCheckResponse>("/api/health")),
  trace: (agent: string, limit: number) =>
    load(`the ${cap(agent)} trace`, () => getData<AgentLogEntry[]>(`/api/trace?limit=${limit}&agent=${encodeURIComponent(agent)}`)),
  stock: () => load("the stock snapshot", () => fetchTruthTable<StockLotRow>("stock")),
  materials: () => load("the material master", () => fetchTruthTable<MaterialRow>("materials")),
  bom: () => load("the BOM", () => fetchTruthTable<BomRow>("bom")),
  suppliers: () => load("the supplier profiles", () => fetchTruthTable<SupplierRow>("suppliers")),
  partMap: () => load("the supplier part lists", () => fetchTruthTable<{ supplier_id: string; component_id: string }>("supplier_part_map")),
  shipments: () => load("the shipments", () => fetchTruthTable<ShipmentRow>("shipments")),
  journey: (material?: string) => load("the PO lines", () => material
    ? getData<JourneyRow[]>(`/api/part-journey?material_id=${encodeURIComponent(material)}&limit=5000`)
    : fetchJourney()),
  truth: () => load("the truth store", loadTruth),
};

/** Shares one fetch between the steps of a run. */
function once<T>(fn: () => Promise<T>): () => Promise<T> {
  let p: Promise<T> | undefined;
  return () => (p ??= fn());
}

// ---------------------------------------------------------------------------
// Wording helpers
// ---------------------------------------------------------------------------
const BERLIN = "Europe/Berlin";
const sum = (xs: number[]) => xs.reduce((a, b) => a + b, 0);
const uniq = <T>(xs: T[]): T[] => [...new Set(xs)];
const plural = (n: number, one: string, many = `${one}s`) => `${fmt(n)} ${n === 1 ? one : many}`;
const isAre = (n: number, one: string, many = `${one}s`) => `${plural(n, one, many)} ${n === 1 ? "is" : "are"}`;
const human = (s: string | null | undefined) => (s ?? "").replace(/_/g, " ").trim();
const cap = (s: string) => (s ? s[0].toUpperCase() + s.slice(1) : s);
const trimDot = (s: string) => s.trim().replace(/[.\s]+$/, "");
const clip = (s: string, n = 36) => (s.length > n ? `${s.slice(0, n - 1).trimEnd()}…` : s);
const sentences = (xs: (string | false | null | undefined)[]) => xs.filter(Boolean).join(" ");
const fileName = (path: string | null | undefined) => sourceFile(path).split("/").pop() || "";
const weeks = (w: number | null) => (w == null ? "–" : w.toFixed(1));
/** "Game Card 128 GB – Grand Heist 6" → "Game Card 128 GB"; drops parentheses. */
const shortName = (desc: string) => desc.split(/\s[–—-]\s/)[0].replace(/\s*\([^)]*\)/g, "").trim() || desc;

function andList(items: string[]): string {
  const xs = items.filter(Boolean);
  return xs.length <= 1 ? xs.join("") : `${xs.slice(0, -1).join(", ")} and ${xs[xs.length - 1]}`;
}

/** Distinct file names for a detail line: "stock_snapshot.csv" or "a.csv, b.csv +2 more". */
function fileList(paths: (string | null | undefined)[], max = 2): string {
  const names = uniq(paths.map(fileName).filter(Boolean));
  if (!names.length) return "no source file";
  return names.length > max ? `${names.slice(0, max).join(", ")} +${names.length - max} more` : names.join(", ");
}

/** File names for a sentence; two files called "profile.json" get their folder added. */
function fileLabels(paths: string[]): string[] {
  const names = paths.map(fileName);
  return paths.map((p, i) => (names.filter((n) => n === names[i]).length > 1 ? `${names[i]} (${fileFolder(p)})` : names[i]));
}

/** "2026-11-27" (or a timestamp) → "Fri 27 Nov", Berlin time. */
function day(d: string | null | undefined): string {
  if (!d) return "–";
  const date = /^\d{4}-\d{2}-\d{2}$/.test(d) ? new Date(`${d}T12:00:00Z`) : parseTs(d);
  return date && !Number.isNaN(date.getTime())
    ? date.toLocaleDateString("en-GB", { weekday: "short", day: "numeric", month: "short", timeZone: BERLIN })
    : d;
}

/** Launch deadline as "Sun 20:00", Berlin time. */
function deadline(ts: string | null | undefined): string {
  const d = parseTs(ts);
  if (!d || Number.isNaN(d.getTime())) return "the deadline";
  const wd = d.toLocaleDateString("en-GB", { weekday: "short", timeZone: BERLIN });
  return `${wd} ${d.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit", timeZone: BERLIN })}`;
}

/** When the portal drop was cut ("…T17-23…" → "17:23"), else when it was checked. */
function dropTime(d: DropRecord): string {
  const t = (d.label ?? "").match(/T(\d{2})-(\d{2})/);
  return t ? `${t[1]}:${t[2]}` : clock(d.checked_at).slice(0, 5);
}

/** "CN" → "China", "zh" → "Chinese" (falls back to the code). */
function displayName(code: string, type: "language" | "region"): string {
  try {
    return (code && new Intl.DisplayNames(["en"], { type }).of(code)) || code;
  } catch {
    return code;
  }
}

// ---------------------------------------------------------------------------
// Answer building blocks
// ---------------------------------------------------------------------------
type Block = AnswerBlock | null | false;
const cell = (value: string, tone?: CellTone): AnswerCell => (tone ? { value, tone } : value);
const text = (lead?: string, body?: string): AnswerBlock => ({ kind: "text", ...(lead ? { lead } : {}), ...(body ? { text: body } : {}) });
const table = (cols: [string, "right"?][], rows: AnswerCell[][]): AnswerBlock =>
  ({ kind: "table", columns: cols.map(([label, align]) => (align ? { label, align } : { label })), rows });
const bullets = (items: string[]): AnswerBlock => ({ kind: "bullets", items });
const step = (id: string, title: string, tags: string[], run: () => Promise<string | undefined>): RunStep => ({ id, title, tags, run });

function answer(title: string, blocks: Block[], highlight: string[], followUps: string[]): AgentAnswer {
  return {
    title,
    blocks: blocks.filter((b): b is AnswerBlock => !!b && (b.kind !== "text" || !!(b.lead || b.text))),
    highlight: uniq(highlight.filter(Boolean)),
    followUps: uniq(followUps.filter(Boolean)).slice(0, 3),
  };
}

const COVER_TONE: Record<string, CellTone> = { red: "stop", amber: "warn", green: "ok", none: "muted" };
const COVER_WORD: Record<string, string> = { red: "in the red", amber: "amber", green: "green", none: "" };
const CHANGE_TONE: Record<string, CellTone> = { added: "ok", changed: "warn", removed: "stop" };
const SHIP_WORDS: Record<string, string> = {
  in_transit: "in transit", arrived_port: "at the port", in_delivery: "out for delivery", delayed: "delayed", booked: "booked",
};
const shipWord = (s: string | null | undefined) => SHIP_WORDS[s ?? ""] ?? (human(s) || "unknown");
const shipTone = (s: string | null | undefined): CellTone | undefined =>
  s === "delayed" ? "stop" : s === "arrived_port" || s === "in_delivery" ? "ok" : s === "booked" ? "muted" : undefined;

// ---------------------------------------------------------------------------
// Data helpers
// ---------------------------------------------------------------------------
type Obj = Record<string, unknown>;
const objs = (v: unknown): Obj[] => (Array.isArray(v) ? v.filter((x): x is Obj => !!x && typeof x === "object") : []);
const isDue = (l: PoLine, close: string | null) => !!close && !!l.due && l.due <= close;
const byDue = (a: PoLine, b: PoLine) => (a.due ?? "~").localeCompare(b.due ?? "~") || a.key.localeCompare(b.key);
/** Step of a PO line; with a single shipment on the PO its status is added ("shipped · in transit"). */
const stepOf = (l: PoLine) =>
  l.last_step === "shipped" && l.shipments.length === 1 && l.shipments[0].status ? `shipped · ${shipWord(l.shipments[0].status)}` : human(l.last_step);

/** Shipments by id; per id the timestamped (ERP) row comes first, other feeds after it. */
function shipmentGroups(rows: ShipmentRow[]): Map<string, ShipmentRow[]> {
  const m = new Map<string, ShipmentRow[]>();
  for (const s of rows) m.set(s.shipment_id, [...(m.get(s.shipment_id) ?? []), s]);
  for (const list of m.values()) list.sort((a, b) => (b.updated_at ?? "").localeCompare(a.updated_at ?? ""));
  return m;
}

/** "part|batch" → the lot as the newest forecasts see it: status ("eligible", "revision blocked", …) and revision. */
function forecastLots(forecast: Forecast[]): Map<string, { status: string; revision: string }> {
  const m = new Map<string, { status: string; revision: string }>();
  for (const f of newestForecasts(forecast)) {
    for (const l of objs(f.details?.lots)) {
      m.set(`${String(l.part)}|${String(l.batch ?? "")}`, { status: String(l.status ?? ""), revision: String(l.revision ?? "") });
    }
  }
  return m;
}

/** "63 → 105"; a note that only grew ("old | new text") shows what was added. Numbers always show old → new. */
function oldNew(o: string | null, n: string | null, max = 24): string {
  const added = o && n && n.startsWith(o) ? n.slice(o.length) : "";
  if (o && /^[\s|;,.]/.test(added) && added.replace(/[\s|;,.]+/g, "") && !/^[\d\s.,+-]+$/.test(o)) {
    return `${clip(o, max - 4)} + ${clip(added.replace(/^[\s|;,.]+/, ""), max)}`;
  }
  return `${clip(o || "empty", max)} → ${clip(n || "empty", max)}`;
}

/** Latest rating per entity (highest id wins), like the Risk Rater report. */
function latestRisks(risk: RiskScore[], type: string): RiskScore[] {
  const m = new Map<string, RiskScore>();
  for (const r of risk) if (String(r.entity_type) === type && (m.get(r.entity_id)?.id ?? -1) < r.id) m.set(r.entity_id, r);
  return [...m.values()];
}

function mostCommon(xs: string[]): string | undefined {
  const counts = new Map<string, number>();
  for (const x of xs.filter(Boolean)) counts.set(x, (counts.get(x) ?? 0) + 1);
  return [...counts].sort((a, b) => b[1] - a[1])[0]?.[0];
}

const openFindings = (issues: Issue[], test: (i: Issue) => boolean) =>
  issues.filter((i) => i.status !== "resolved" && test(i)).sort((a, b) => b.severity - a.severity || b.id - a.id);
const mentions = (i: Issue, token: string) =>
  [i.summary, i.reason, ...(i.evidence ?? []).flatMap((e) => [e.path, e.locator])].some((s) => (s ?? "").includes(token));
const citesFile = (i: Issue, paths: Set<string>) => (i.evidence ?? []).some((e) => paths.has(sourceFile(e.path)));
const nextStep = (found: Issue[]) => {
  const label = found.flatMap((i) => i.suggested_actions ?? []).find((a) => a.label)?.label;
  return label ? `Next step from the findings: ${trimDot(label)}.` : "";
};
/** "4 open findings on it:" followed by the most severe ones. */
const findingBlocks = (found: Issue[], on: string, max = 4): Block[] =>
  found.length ? [text(undefined, `${plural(found.length, "open finding")} ${on}:`), bullets(found.slice(0, max).map((i) => `Sev ${i.severity} · ${i.summary}`))] : [];

const describe = (mats: MaterialRow[], id: string) => mats.find((m) => m.component_id === id)?.description ?? "";
/** A change-log entry about a part: its material row or a supplier/part pair like "SUP-…/CMP-…". */
const touchesPart = (c: FieldChange, id: string) => (c.entity_key ?? "").split("/").includes(id);
/** Change-log rows Ingest wrote (the answers say "Ingest changed …"). */
const byIngest = (changes: FieldChange[]) => changes.filter((c) => !c.agent || c.agent === "ingest");
const truthOf = (part: Partial<TruthData>): TruthData => ({
  materials: [], stock: [], journey: [], poLines: [], bom: [], suppliers: [], shipments: [], products: [],
  engineeringChanges: [], confirmations: [], sources: {}, tableCounts: {}, ...part,
});

const dropPaths = (d: DropRecord) => [...d.added, ...d.changed, ...d.removed];
const allPaths = (drops: DropRecord[]) => uniq(drops.flatMap(dropPaths));
const newestDrop = (drops: DropRecord[]): DropRecord | undefined => [...drops].sort((a, b) => b.id - a.id)[0];
const changeIn = (d: DropRecord, p: string) =>
  d.added.includes(p) ? "added" : d.changed.includes(p) ? "changed" : d.removed.includes(p) ? "removed" : null;

// ---------------------------------------------------------------------------
// Question matching
// ---------------------------------------------------------------------------
const PART_RE = /\b[A-Z]{2,5}-[A-Z]{2,5}-\d{2,5}\b/;
const SUP_RE = /\bSUP-\d{2,6}\b/;
const SHIPMENT_RE = /\bSHP-\d{4}-\d{2,6}\b/;
const TEN_DIGIT_RE = /\b(\d{10})\b(?:\s*(?:\/|line)\s*(\d{1,3})\b)?/i;
const ERRORS_RE = /\b(errors?|fail\w*|crash\w*|exceptions?|broken|went wrong|problems?|glitch\w*)\b/;
const CHANGES_RE = /\b(chang\w*|correct\w*|fill(s|ed)?|overwr\w*|fix(es|ed)?|edit\w*|modif\w*)\b/;
/** "What did you update?" asks for changes; "the latest update on the launch" asks about the launch. */
const UPDATE_RE = /\bupdat\w*/;
const LOW_RE = /\b(low(est)?|below|under|less than|fewer than|red|amber|run(s|ning)? (out|low)|weeks?|wks?)\b/;
const LAUNCH_RE = /\b(enough|launch\w*|short\w*|make it|on track|deadline|finish\w*|pack(s|ed|ing)?|cop(y|ies)|editions?|cover\w*|limit\w*|bottleneck|forecast|sunday|stock)\b/;
const REMOVED_RE = /\b(remov\w*|delet\w*|gone|disappear\w*|missing)\b/;
const HISTORY_RE = /\b(how many|history|so far|in total|total|all (the )?drops|every drop|timeline|overall|when)\b/;
const LATEST_RE = /\b(new\w*|latest|last|recent\w*|chang\w*|added|arriv\w*|came in|portal|drops?|files?|today|updat\w*)\b/;
const DROP_NO_RE = /\bdrop\s*#?\s*(\d{1,4})\b/;
const FILE_RE = /[\w-]+\.(csv|xlsx?|pdf|json|jpe?g|png|docx?|md|txt|eml)\b/;
const NUMBER_WORDS: Record<string, number> = { one: 1, two: 2, three: 3, four: 4, five: 5, six: 6 };
/** Generic company-name words that never identify one supplier on their own. */
const COMPANY_WORDS = new Set([
  "works", "group", "company", "limited", "holding", "holdings", "industries", "industrial", "international", "trading",
  "packaging", "supply", "supplies", "systems", "solutions", "technology", "technologies", "electronics", "manufacturing",
  "semi", "parts", "components", "products",
]);
const WORD_RE = /[a-z0-9À-ɏ]+/g;

function findPart(upper: string, truth: TruthData | null): string | undefined {
  const known = truth?.materials.map((m) => m.component_id) ?? [];
  const exact = known.find((id) => upper.includes(id.toUpperCase()));
  if (exact) return exact;
  const ecn = truth?.engineeringChanges.find((c) => c.change_id && upper.includes(c.change_id.toUpperCase()));
  if (ecn?.child_id) return ecn.child_id;
  const shaped = upper.match(PART_RE)?.[0];
  if (shaped) return shaped;
  for (const t of upper.match(/\b[A-Z]{2,5}-\d{2,5}\b/g) ?? []) {
    const hits = known.filter((id) => id.toUpperCase().endsWith(`-${t}`));
    if (hits.length === 1) return hits[0]; // "GAM-004" → the one known id ending in it
  }
  return undefined;
}

/** A SUP id, a supplier's (legal) name, or a distinctive word of its name (a word no other supplier's name uses). */
function findSupplier(lower: string, upper: string, truth: TruthData | null): string | undefined {
  const id = upper.match(SUP_RE)?.[0];
  if (id) return id;
  const suppliers = truth?.suppliers ?? [];
  const full = suppliers.find((s) => [s.name, s.legal_name].some((n) => n && lower.includes(n.toLowerCase())));
  if (full) return full.supplier_id;
  const tokens = (s: SupplierRow) =>
    uniq((s.name ?? "").toLowerCase().match(WORD_RE) ?? []).filter((t) => t.length >= 4 && !COMPANY_WORDS.has(t));
  const owners = new Map<string, number>();
  for (const s of suppliers) for (const t of tokens(s)) owners.set(t, (owners.get(t) ?? 0) + 1);
  const words = new Set(lower.match(WORD_RE) ?? []);
  let best: { id: string; score: number } | undefined;
  for (const s of suppliers) {
    const score = tokens(s).filter((t) => words.has(t) && owners.get(t) === 1).length;
    if (score > (best?.score ?? 0)) best = { id: s.supplier_id, score };
  }
  return best?.id;
}

/** "below 3 weeks" → 3; "amber" → 4 (amber and red); default 2 (red). */
function threshold(lower: string): number {
  const m = lower.match(/(?:below|under|less than|fewer than|<)\s*(\d+(?:\.\d+)?|one|two|three|four|five|six)\s*(?:weeks?|wks?|w\b)/);
  if (m) return NUMBER_WORDS[m[1]] ?? Number(m[1]);
  return /\bamber\b/.test(lower) ? 4 : 2;
}

/** Portal files the question names: full path > file name > name without extension/date, or a path fragment. */
function matchFiles(lower: string, paths: string[]): string[] {
  const tokens = (lower.match(/[a-z0-9_./-]{5,}/g) ?? []).map((t) => t.replace(/[./-]+$/, "")).filter((t) => t.length >= 5 && /[./_]/.test(t));
  let best = 0;
  let hits: string[] = [];
  for (const p of paths) {
    const lp = p.toLowerCase();
    const base = lp.split("/").pop() ?? lp;
    const stem = base.replace(/\.[a-z0-9]+$/, "");
    const core = stem.replace(/^\d{4}-?\d{2}-?\d{2}[_-]+/, "").replace(/[_-]+\d{4}-?\d{2}-?\d{2}$/, "");
    const named = [stem, core].some((n) => n.length >= 6 && (lower.includes(n) || lower.includes(n.replace(/[_-]+/g, " "))));
    const score = lower.includes(lp) ? 3 : lower.includes(base) ? 2 : named || tokens.some((t) => lp.includes(t)) ? 1 : 0;
    if (score > best) [best, hits] = [score, [p]];
    else if (score && score === best) hits.push(p);
  }
  return hits;
}

// ---------------------------------------------------------------------------
// Ingest: will every launch copy be packed by the deadline?
// ---------------------------------------------------------------------------
function planLaunchCover(ctx: BrainContext): PlannedRun {
  const forecastP = once(api.forecast);
  const s = { forecasts: [] as Forecast[], proposed: 0, bom: [] as BomRow[], mats: [] as MaterialRow[] };
  const limiting = (all: Forecast[]) => uniq(newestForecasts(all).map((f) => f.limiting_part).filter(Boolean));
  const both = newestForecasts(ctx.snap.forecast).length === 2;

  const steps = [
    step("forecast", "Pulled the Launch Planner forecast", ["Planner"], async () => {
      const [all, cards] = await Promise.all([forecastP(), api.proposed().catch(() => null)]);
      s.forecasts = newestForecasts(all).sort((a, b) => (b.still_to_pack ?? 0) - (a.still_to_pack ?? 0));
      s.proposed = cards ? cards.length : ctx.snap.decisions.filter((d) => d.status === "proposed").length;
      if (!s.forecasts.length) return "No forecast yet";
      return s.forecasts.map((f) => `${f.edition} ${(f.shortfall ?? 0) > 0 ? `${fmt(f.shortfall)} short` : "on track"}`).join(" · ");
    }),
    step("stock", "Read the stock snapshot", ["truth store"], async () => {
      const [stock, all] = await Promise.all([api.stock(), forecastP()]);
      const focus = limiting(all).map((p) => `${fmt(sum(stock.filter((l) => l.material_id === p).map((l) => l.quantity || 0)))} ${p}`);
      return [plural(stock.length, "lot"), ...focus, fileList(stock.map((l) => l.source))].join(" · ");
    }),
    step("bom", both ? "Matched both launch BOMs" : "Matched the launch BOMs", ["truth store"], async () => {
      const [bom, mats, all] = await Promise.all([api.bom(), api.materials(), forecastP()]);
      Object.assign(s, { bom, mats });
      const products = new Set(newestForecasts(all).map((f) => f.product_id));
      const used = bom.filter((b) => products.has(b.parent_id));
      return `${plural(used.length, "BOM line")} · ${plural(uniq(used.map((b) => b.child_id)).length, "part")} · ${fileList(used.map((b) => b.source))}`;
    }),
    step("pos", "Checked open POs due by close", ["truth store"], async () => {
      const [rows, all] = await Promise.all([api.journey(), forecastP()]);
      const parts = limiting(all);
      if (!parts.length) return "No limiting part to check";
      const open = poLines(rows.filter((j) => parts.includes(j.material_id))).filter(isOpenPoLine);
      const close = closeDate(all);
      const due = sum(open.filter((l) => isDue(l, close)).map((l) => l.open));
      return `${plural(open.length, "open line")} for ${andList(parts)} · ${fmt(due)} due by ${close ? day(close) : "close"}`;
    }),
  ];

  const compose = (): AgentAnswer => {
    const title = "Checked stock, open POs and the forecast";
    const { forecasts, bom, mats, proposed: n } = s;
    const follow = (part?: string) => [part ? `Show ${part}` : "", "Which parts are below 2 weeks cover?", "What did Ingest change last?"];
    if (!forecasts.length) {
      return answer(title, [text("I can't tell yet — the Launch Planner hasn't published a forecast.", "Once it has, I'll check stock and open POs against it.")], [], follow());
    }
    const short = forecasts.filter((f) => (f.shortfall ?? 0) > 0);
    const total = sum(short.map((f) => f.shortfall ?? 0));
    const by = deadline(forecasts.find((f) => f.deadline)?.deadline);
    const grid = table([["Edition"], ["Need", "right"], ["Have", "right"], ["Short", "right"]], forecasts.map((f) => {
      const [need, gap] = [f.still_to_pack ?? 0, f.shortfall ?? 0];
      return [f.edition, fmt(need), fmt(Math.max(0, need - gap)), cell(fmt(gap), gap > 0 ? "stop" : "ok")];
    }));
    const cards = n ? `${isAre(n, "decision card")} proposed and waiting — ${n === 1 ? "it's" : "they're"} ready in the control room.` : "No decision cards are waiting.";
    if (!total) {
      const parts = uniq(forecasts.map((f) => f.limiting_part).filter(Boolean));
      return answer(title, [
        text("Yes — every launch copy is covered.", `The Planner expects all ${fmt(sum(forecasts.map((f) => f.still_to_pack ?? 0)))} copies still to pack to be done by ${by}.`),
        grid,
        text(undefined, `Nothing to expedite. ${cards}`),
      ], parts, follow(parts[0]));
    }
    const why = short.map((f, i) => {
      const d = describe(mats, f.limiting_part);
      const what = f.limiting_part ? (d ? `${shortName(d)} (${f.limiting_part})` : f.limiting_part) : "a part the Planner didn't name";
      return i === 0 ? `${f.edition} runs short on ${what}` : `${f.edition} on ${what}`;
    });
    // Units to expedite: copies short × BOM quantity per copy, per limiting part.
    const asks = new Map<string, { units: number; editions: string[] }>();
    for (const f of short.filter((x) => x.limiting_part)) {
      const a = asks.get(f.limiting_part) ?? { units: 0, editions: [] };
      a.units += (f.shortfall ?? 0) * (bom.find((b) => b.parent_id === f.product_id && b.child_id === f.limiting_part)?.qty_per || 1);
      a.editions.push(f.edition);
      asks.set(f.limiting_part, a);
    }
    const expedite = [...asks].map(([id, a]) => {
      const d = describe(mats, id);
      return `**${fmt(a.units)} units of ${d ? `${shortName(d)} (${id})` : id}** for ${andList(a.editions)}`;
    });
    const parts = [...asks.keys()];
    return answer(title, [
      text(`No — ${fmt(total)} copies won't be packed by ${by}.`, `${andList(why)}.`),
      grid,
      text(undefined, sentences([expedite.length > 0 && `I'd expedite ${andList(expedite)}.`, cards])),
    ], parts, follow(parts[0]));
  };
  return { intent: "launch_cover", workingTitle: "Checking stock against the launch", steps, compose };
}

// ---------------------------------------------------------------------------
// Ingest: one part across stock, POs, findings, risk and the change log
// ---------------------------------------------------------------------------
function planPart(ctx: BrainContext, id: string): PlannedRun {
  const forecastP = once(api.forecast);
  const s = {
    mats: [] as MaterialRow[], lots: [] as StockLotRow[], bom: [] as BomRow[], forecast: [] as Forecast[], lines: [] as PoLine[],
    close: null as string | null, issues: [] as Issue[], changes: [] as FieldChange[], risk: [] as RiskScore[],
  };
  const steps = [
    step("stock", "Read the stock snapshot", ["truth store"], async () => {
      const [mats, stock, bom, forecast] = await Promise.all([api.materials(), api.stock(), api.bom(), forecastP()]);
      Object.assign(s, { mats, bom, forecast, lots: stock.filter((l) => l.material_id === id) });
      if (!s.lots.length) return "No lots in the stock snapshot";
      return `${fmt(sum(s.lots.map((l) => l.quantity || 0)))} in ${plural(s.lots.length, "lot")} · ${fileList(s.lots.map((l) => l.source))}`;
    }),
    step("pos", "Checked open POs due by close", ["truth store"], async () => {
      const [rows, forecast] = await Promise.all([api.journey(id), forecastP()]);
      Object.assign(s, { lines: poLines(rows), close: closeDate(forecast) });
      const open = s.lines.filter(isOpenPoLine);
      const due = sum(open.filter((l) => isDue(l, s.close)).map((l) => l.open));
      return `${plural(open.length, "open line")} · ${fmt(due)} due by ${s.close ? day(s.close) : "close"} · ${fmt(sum(open.map((l) => l.open)) - due)} later`;
    }),
    step("findings", "Pulled findings, risk and the change log", ["Reconciler", "Risk Rater", "change log"], async () => {
      const [issues, changes, risk] = await Promise.all([api.issues(), api.changes(), api.risk()]);
      Object.assign(s, { issues, changes, risk });
      return `${plural(openFindings(issues, (i) => i.part_id === id).length, "open finding")} · ${plural(changes.filter((c) => touchesPart(c, id)).length, "field change")}`;
    }),
  ];

  const compose = (): AgentAnswer => {
    const title = `Looked up ${id} across stock, POs and findings`;
    const follow = ["Which parts are below 2 weeks cover?", "Do we have enough stock to finish the launch?"];
    const m = s.mats.find((x) => x.component_id === id);
    if (!m) {
      return answer(title, [text(`I can't find ${id} in the material master.`, s.lots.length || s.lines.length
        ? `Still, the stock snapshot has ${plural(s.lots.length, "lot")} and the POs ${plural(s.lines.length, "line")} for it.`
        : "No stock lot or PO line mentions it either.")], [], follow);
    }
    // Same numbers as the part's row in the table on the left.
    const row = buildPartRows(truthOf({ materials: [m], stock: s.lots, bom: s.bom, poLines: s.lines }), { ...ctx.snap, forecast: s.forecast, issues: s.issues })[0];
    const lots = forecastLots(s.forecast);
    const lotOf = (l: StockLotRow) => lots.get(`${id}|${l.batch ?? ""}`);
    const statusOf = (l: StockLotRow) => lotOf(l)?.status || undefined;
    const blocked = s.lots.filter((l) => (statusOf(l) ?? "eligible") !== "eligible");
    const limits = newestForecasts(s.forecast).filter((f) => f.limiting_part === id && (f.shortfall ?? 0) > 0);
    const risk = latestRisks(s.risk, "part").find((r) => r.entity_id === id);
    const wk = row.weeksCover;
    const open = s.lines.filter(isOpenPoLine).sort(byDue);
    const found = openFindings(s.issues, (i) => i.part_id === id);
    const change = s.changes.filter((c) => touchesPart(c, id)).sort((a, b) => b.id - a.id)[0];
    const sup = mostCommon(s.lines.map((l) => l.supplier_id));
    const supName = ctx.truth?.suppliers.find((x) => x.supplier_id === sup)?.name || sup;
    const closeLabel = s.close ? day(s.close) : "close";
    const about = `${m.description}${m.critical ? ", a critical part" : ""}${m.drawing_rev ? ` on drawing rev ${m.drawing_rev}` : ""}`;
    return answer(title, [
      text(`${id} has ${fmt(row.freeStock)} in stock${wk == null ? "." : ` — ${weeks(wk)} weeks of cover, ${COVER_WORD[coverTone(wk)]}.`}`, sentences([
        `${about}${row.editions.length ? `, used in ${andList(row.editions)}` : ""}.`,
        limits.length > 0 && `It's what limits ${andList(limits.map((f) => `${f.edition} (${fmt(f.shortfall)} copies short)`))}.`,
        blocked.length > 0 && `${fmt(sum(blocked.map((l) => l.quantity || 0)))} of the stock isn't usable (${andList(uniq(blocked.map((l) => statusOf(l) ?? "")))}).`,
        risk && `Risk Rater: ${risk.rating} (${risk.grade}/6).`,
      ])),
      s.lots.length
        ? table([["Batch"], ["Qty", "right"], ["Revision"], ["Status"]], s.lots.map((l) => {
            const st = statusOf(l);
            return [l.batch || "–", fmt(l.quantity), l.revision || lotOf(l)?.revision || "–", st ? cell(st, st === "eligible" ? "ok" : "stop") : cell("–", "muted")];
          }))
        : text(undefined, "There are no lots of it in the stock snapshot."),
      text(undefined, open.length
        ? `${plural(open.length, "open PO line")}: ${fmt(row.onPoByClose)} due by ${closeLabel}, ${fmt(row.openPoQty - row.onPoByClose)} after.${open.length > 6 ? " The next 6:" : ""}`
        : "Nothing is on order: there's no open PO line for it."),
      open.length > 0 && table([["PO"], ["Open qty", "right"], ["Due"], ["Step"]], open.slice(0, 6).map((l) =>
        [l.key, fmt(l.open), isDue(l, s.close) ? cell(day(l.due), "ok") : day(l.due), stepOf(l) || "–"])),
      ...findingBlocks(found, "on it"),
      text(undefined, sentences([
        row.aiOrder > 0 && `I'd expedite **${fmt(row.aiOrder)} units** to close the gap.`,
        nextStep(found),
        change && `Ingest last changed ${change.table_name === "materials" ? `its ${human(change.field)}`
          : `the ${human(change.field)} on ${change.entity_key} (${human(change.table_name)})`}: ${oldNew(change.old_value, change.new_value, 32)} (${fileName(change.source)}).`,
      ])),
    ], [id], [sup ? `Tell me about ${supName}` : "", ...follow]);
  };
  return { intent: "part_detail", workingTitle: `Looking up ${id}`, steps, compose };
}

// ---------------------------------------------------------------------------
// Ingest: parts low on weeks of cover
// ---------------------------------------------------------------------------
function planLowCover(ctx: BrainContext, below: number): PlannedRun {
  const s = { parts: [] as PartRow[], close: null as string | null };
  const baseP = once(async () => {
    const [materials, stock] = await Promise.all([api.materials(), api.stock()]);
    return { materials, stock, parts: buildPartRows(truthOf({ materials, stock }), { ...ctx.snap, forecast: [] }) };
  });
  const lowOf = (parts: PartRow[]) => parts.filter((p) => p.weeksCover != null && p.weeksCover < below);
  const steps = [
    step("stock", "Read the stock snapshot", ["truth store"], async () => {
      const { stock, parts } = await baseP();
      s.parts = parts;
      return `${plural(stock.length, "lot")} · ${plural(parts.filter((p) => p.weeksCover != null).length, "part")} with a cover figure · ${fileList(stock.map((l) => l.source))}`;
    }),
    step("rank", "Ranked parts by weeks of cover", ["truth store"], async () => {
      const low = lowOf((await baseP()).parts);
      return low.length ? `${plural(low.length, "part")} below ${below} weeks · tightest ${low[0].id} at ${weeks(low[0].weeksCover)}` : `None below ${below} weeks`;
    }),
    step("pos", "Checked open POs for them", ["truth store"], async () => {
      const [{ materials, stock }, journey, forecast] = await Promise.all([baseP(), api.journey(), api.forecast()]);
      s.close = closeDate(forecast);
      s.parts = buildPartRows(truthOf({ materials, stock, poLines: poLines(journey) }), { ...ctx.snap, forecast });
      const low = lowOf(s.parts);
      return `${plural(sum(low.map((p) => p.openLines)), "open line")} · ${fmt(sum(low.map((p) => p.onPoByClose)))} due by ${s.close ? day(s.close) : "close"}`;
    }),
  ];

  const compose = (): AgentAnswer => {
    const title = "Ranked parts by weeks of cover";
    const follow = ["Do we have enough stock to finish the launch?", "What did Ingest change last?"];
    const low = lowOf(s.parts);
    if (!s.parts.length) return answer(title, [text("I can't rank parts yet — the material master is empty.")], [], follow);
    if (!low.length) {
      const first = s.parts.find((p) => p.weeksCover != null);
      return answer(title, [text(`No part is below ${below} weeks of cover.`,
        first ? `The lowest is ${first.id} (${first.description}) at ${weeks(first.weeksCover)} weeks.` : "No part has a cover figure.")],
      first ? [first.id] : [], [first ? `Show ${first.id}` : "", ...follow]);
    }
    const t = low[0];
    const critical = low.filter((p) => p.critical).length;
    const noFigure = s.parts.filter((p) => p.weeksCover == null).length;
    const noPo = low.filter((p) => !p.openLines);
    const arriving = low.filter((p) => p.onPoByClose > 0).length;
    const by = s.close ? day(s.close) : "";
    return answer(title, [
      text(`${isAre(low.length, "part")} below ${below} weeks of cover.`, sentences([
        `The tightest is ${t.id} (${t.description}) at ${weeks(t.weeksCover)} weeks, ${t.freeStock ? `with ${fmt(t.freeStock)} in stock` : "with nothing in stock"}.`,
        critical > 0 && (low.length === 1 ? "It's marked critical." : `${fmt(critical)} of them ${critical === 1 ? "is" : "are"} marked critical.`),
        noFigure > 0 && `${plural(noFigure, "part")} ${noFigure === 1 ? "has" : "have"} no cover figure.`,
      ])),
      table([["Part"], ["Description"], ["Weeks cover", "right"], ["Free stock", "right"]], low.map((p) =>
        [p.id, shortName(p.description), cell(weeks(p.weeksCover), COVER_TONE[coverTone(p.weeksCover)]), fmt(p.freeStock)])),
      text(undefined, sentences([
        noPo.length > 0 && `${andList(noPo.map((p) => `**${p.id}**`))} ${noPo.length === 1 ? "has" : "have"} no open PO line at all.`,
        !!by && (arriving === low.length ? `All ${fmt(low.length)} have something due by ${by}.`
          : `${arriving ? `Only ${fmt(arriving)} of the ${fmt(low.length)}` : `None of the ${fmt(low.length)}`} ${arriving === 1 ? "has" : "have"} anything due by ${by}.`),
      ])),
    ], low.map((p) => p.id), [`Show ${t.id}`, ...follow]);
  };
  return { intent: "low_cover", workingTitle: "Ranking parts by weeks of cover", steps, compose };
}

// ---------------------------------------------------------------------------
// Ingest: what it changed in the truth store
// ---------------------------------------------------------------------------
function planChanges(ctx: BrainContext, lower: string): PlannedRun {
  const s = { changes: [] as FieldChange[], trace: [] as AgentLogEntry[] };
  const steps = [
    step("log", "Read the change log", ["change log"], async () => {
      s.changes = byIngest(await api.changes()).sort((a, b) => b.id - a.id);
      const fills = s.changes.filter((c) => c.change_type === "fill").length;
      return `${plural(s.changes.length, "value")} · ${fmt(fills)} filled · ${fmt(s.changes.length - fills)} corrected`;
    }),
    step("trace", "Checked the Ingest trace", ["trace"], async () => {
      s.trace = (await api.trace("ingest", 300)).sort((a, b) => b.id - a.id);
      return s.trace.find((e) => e.action === "drop_ingested")?.detail || plural(s.trace.length, "entry", "entries");
    }),
  ];

  const compose = (): AgentAnswer => {
    const title = "Read the change log";
    const follow = ["Do we have enough stock to finish the launch?", "Any errors?"];
    // Narrow to a table ("in products") or a kind ("filled", "corrected") when the question names one.
    const tables: string[] = TRUTH_TABLES.filter((t) => new RegExp(`\\b${human(t)}\\b`).test(lower));
    const kind = /\bfill/.test(lower) ? "fill" : /correct|overwr/.test(lower) ? "overwrite" : null;
    const list = s.changes.filter((c) => (!tables.length || tables.includes(c.table_name)) && (!kind || c.change_type === kind));
    const where = tables.length ? `the ${andList(tables.map(human))} table${tables.length > 1 ? "s" : ""}` : "the truth store";
    if (!list.length) {
      return answer(title, [s.changes.length
        ? text(`No ${kind === "fill" ? "filled" : kind ? "corrected" : "changed"} values in ${where}.`, `Ingest changed ${plural(s.changes.length, "value")} in total.`)
        : text("Ingest hasn't changed any values in the truth store yet.", "Every row is still exactly as it was first read from its file.")],
      [], ["Which parts are below 2 weeks cover?", ...follow]);
    }
    const fills = list.filter((c) => c.change_type === "fill").length;
    const files = fileLabels(uniq(list.map((c) => sourceFile(c.source)).filter(Boolean)));
    const named = files.length > 5 ? [...files.slice(0, 5), `${fmt(files.length - 5)} more`] : files;
    const last = s.trace.find((e) => e.action === "drop_ingested");
    const { rows_inserted: ins, rows_updated: upd } = (last?.data ?? {}) as Obj;
    const fileCount = (last?.detail ?? "").match(/^(\d+) files?/)?.[1];
    const lastDrop = typeof ins === "number" && typeof upd === "number"
      && `In the last drop it added ${plural(ins, "row")} and updated ${fmt(upd)}${fileCount ? ` from ${plural(Number(fileCount), "file")}` : ""}.`;
    const known = new Set(ctx.truth?.materials.map((m) => m.component_id) ?? []);
    const parts = uniq(list.flatMap((c) => (c.entity_key ?? "").split("/")).filter((k) => (known.size ? known.has(k) : PART_RE.test(k))));
    return answer(title, [
      text(`Ingest ${kind === "fill" ? "filled" : kind ? "corrected" : "changed"} ${plural(list.length, "value")} in ${where}.`, sentences([
        !kind && `${fmt(fills)} were empty fields it filled and ${fmt(list.length - fills)} were corrections to existing values.`,
        `They came from ${plural(files.length, "file")}: ${andList(named)}.`,
      ])),
      table([["Entity"], ["Field"], ["Old → New"], ["Source"]], list.slice(0, 6).map((c) => [
        c.entity_key || `${c.table_name} #${c.row_id}`,
        human(c.field),
        cell(oldNew(c.old_value, c.new_value), c.change_type === "fill" ? "ok" : "warn"),
        fileName(c.source) || c.source,
      ])),
      text(undefined, sentences([lastDrop, "Each change keeps the old value and the file it came from."])),
    ], parts, [parts[0] ? `Show ${parts[0]}` : "", ...follow]);
  };
  return { intent: "changes", workingTitle: "Reading the change log", steps, compose };
}

// ---------------------------------------------------------------------------
// Ingest: one supplier across profile, POs, shipments, risk, findings and emails
// ---------------------------------------------------------------------------
function planSupplier(id: string, truth: TruthData | null): PlannedRun {
  const s = {
    sup: undefined as SupplierRow | undefined, all: [] as SupplierRow[], mapParts: [] as string[], lines: [] as PoLine[],
    ships: new Map<string, ShipmentRow[]>(), risk: undefined as RiskScore | undefined, found: [] as Issue[], mails: [] as OutboxMessage[],
  };
  const steps = [
    step("profile", "Looked up the supplier profile", ["truth store"], async () => {
      const [all, map] = await Promise.all([api.suppliers(), api.partMap()]);
      Object.assign(s, { all, sup: all.find((x) => x.supplier_id === id), mapParts: map.filter((r) => r.supplier_id === id).map((r) => r.component_id) });
      return s.sup ? `${s.sup.name} · ${[s.sup.city, s.sup.country].filter(Boolean).join(", ")} · ${fileName(s.sup.source)}` : "Not in the supplier profiles";
    }),
    step("pos", "Checked their open POs and shipments", ["truth store"], async () => {
      const [rows, ships] = await Promise.all([api.journey(), api.shipments()]);
      Object.assign(s, { lines: poLines(rows.filter((j) => j.supplier_id === id)), ships: shipmentGroups(ships.filter((x) => x.supplier_id === id)) });
      const open = s.lines.filter(isOpenPoLine);
      return `${plural(open.length, "open PO line")} · ${fmt(sum(open.map((l) => l.open)))} pcs to come · ${plural(s.ships.size, "shipment")}`;
    }),
    step("risk", "Pulled risk, findings and emails", ["Risk Rater", "Chaser"], async () => {
      const [risk, issues, mails] = await Promise.all([api.risk(), api.issues(), api.outbox()]);
      Object.assign(s, {
        risk: latestRisks(risk, "supplier").find((r) => r.entity_id === id),
        found: openFindings(issues, (i) => i.supplier_id === id),
        mails: mails.filter((m) => m.supplier_id === id).sort((a, b) => b.id - a.id),
      });
      return `${s.risk ? `Rated ${s.risk.rating}` : "Not rated"} · ${plural(s.found.length, "open finding")} · ${plural(s.mails.length, "email")}`;
    }),
  ];

  const compose = (): AgentAnswer => {
    const { sup, risk: r, found, mails } = s;
    const name = sup?.name || id;
    const title = `Looked up ${name} across POs, shipments and risk`;
    const follow = ["Do we have enough stock to finish the launch?", "Which parts are below 2 weeks cover?"];
    if (!sup && !s.lines.length) {
      return answer(title, [text(`I can't find ${id} in the supplier profiles.`,
        s.all.length ? `I know ${andList(s.all.map((x) => `${x.name} (${x.supplier_id})`))}.` : "No supplier profiles are loaded yet.")], [], follow);
    }
    const parts = uniq([...s.lines.map((l) => l.material_id), ...s.mapParts]).sort();
    const open = s.lines.filter(isOpenPoLine);
    const where = sup ? [sup.city, displayName(sup.country, "region")].filter(Boolean).join(", ") : "";
    const ships = [...s.ships.values()].sort((a, b) => (a[0].eta ?? "~").localeCompare(b[0].eta ?? "~"));
    const statusCell = (rows: ShipmentRow[]) => {
      const other = rows.find((x) => x.status && x.status !== rows[0].status);
      return cell(`${shipWord(rows[0].status)}${other ? ` · other feed: ${shipWord(other.status)}` : ""}`, shipTone(rows[0].status));
    };
    const suggested = ((r?.reasons?.[0] ?? {}) as ExtendedRiskReason).recommended_action?.label;
    const mail = mails[0];
    const contact = sup?.contact_name ? `; contact ${sup.contact_name}${sup.contact_email ? ` (${sup.contact_email})` : ""}` : "";
    const profile = sup
      && `${sup.legal_name || sup.name}${where ? ` in ${where}` : ""}${sup.language ? `, writing in ${displayName(sup.language, "language")}` : ""}${contact}.`;
    const emailed = mail
      ? `The Chaser has emailed them ${mails.length === 1 ? "once" : `${fmt(mails.length)} times`}; the latest, "${clip(mail.subject, 70)}", is ${mail.status}.`
      : "The Chaser hasn't emailed them yet.";
    return answer(title, [
      text(r ? `${name} (${id}) is rated ${r.rating} (${r.grade}/6)${sup?.status ? ` with status "${sup.status}"` : ""}.`
        : `${name} (${id}) isn't rated by the Risk Rater yet${sup?.status ? `; status "${sup.status}"` : ""}.`, sentences([
        profile,
        `They supply ${plural(parts.length, "part")}${parts.length ? ` (${andList(parts)})` : ""} with ${plural(open.length, "open PO line")}`
          + ` and ${fmt(sum(open.map((l) => l.open)))} pcs still to arrive.`,
      ])),
      ships.length
        ? table([["Shipment"], ["Mode"], ["Status"], ["ETA"]], ships.slice(0, 8).map((rows) =>
            [rows[0].shipment_id, human(rows[0].mode) || "–", statusCell(rows), day(rows[0].eta)]))
        : text(undefined, "No shipments on file for them."),
      ...findingBlocks(found, "on them"),
      text(undefined, sentences([emailed, typeof suggested === "string" && suggested ? `The Risk Rater suggests: ${trimDot(suggested)}.` : nextStep(found)])),
    ], parts, [parts[0] ? `Show ${parts[0]}` : "", ...follow]);
  };
  const known = truth?.suppliers.find((x) => x.supplier_id === id)?.name;
  return { intent: "supplier", workingTitle: `Looking up ${known || id}`, steps, compose };
}

// ---------------------------------------------------------------------------
// Ingest: a PO (optionally one line) or a shipment, traced through the journey
// ---------------------------------------------------------------------------
function planPo(po: string, line?: number): PlannedRun {
  const label = line ? `${po}/${line}` : po;
  const s = { lines: [] as PoLine[], sups: [] as SupplierRow[], found: [] as Issue[] };
  const steps = [
    step("po", "Found the PO lines", ["truth store"], async () => {
      const [rows, sups] = await Promise.all([api.journey(), api.suppliers()]);
      Object.assign(s, { sups, lines: poLines(rows.filter((j) => j.po_number === po && (!line || j.po_line === line))).sort((a, b) => a.po_line - b.po_line) });
      return s.lines.length ? `${plural(s.lines.length, "line")} · ${s.lines[0].supplier_id} · ${fileList(s.lines.map((l) => l.po_source))}` : "No PO lines found";
    }),
    step("findings", "Checked findings that mention it", ["Reconciler"], async () => {
      s.found = openFindings(await api.issues(), (i) => i.po_number === po || mentions(i, po));
      return plural(s.found.length, "open finding");
    }),
  ];
  const compose = (): AgentAnswer => {
    const title = `Traced PO ${label} through confirmation, shipping and receipt`;
    const follow = ["Do we have enough stock to finish the launch?", "Which parts are below 2 weeks cover?"];
    if (!s.lines.length) return answer(title, [text(`I can't find PO ${label} in the purchase orders.`, "It isn't in any ERP file Ingest has read.")], [], follow);
    const supId = s.lines[0].supplier_id;
    const supName = s.sups.find((x) => x.supplier_id === supId)?.name || supId;
    const open = sum(s.lines.filter(isOpenPoLine).map((l) => l.open));
    // The backend links shipments per PO, so they are listed for the PO, not per line.
    const ships = [...new Map(s.lines.flatMap((l) => l.shipments).map((x) => [x.id, x] as const)).values()];
    const shipText = ships.length
      ? `Its ${ships.length === 1 ? "shipment" : "shipments"}: ${andList(ships.map((x) => `${x.id} (${shipWord(x.status)}, ETA ${day(x.eta)})`))}.`
      : "No shipment is booked for it yet.";
    return answer(title, [
      text(`PO ${label} has ${plural(s.lines.length, "line")} from ${supName}: ${open ? `${fmt(open)} still to receive` : "everything received"}.`, shipText),
      table([["Line"], ["Part"], ["Ordered", "right"], ["Confirmed", "right"], ["Received", "right"], ["Step"]], s.lines.map((l) => [
        String(l.po_line), l.material_id, fmt(l.qty_ordered), fmt(l.qty_confirmed), fmt(l.qty_received),
        cell(stepOf(l), l.last_step === "received" ? "ok" : undefined),
      ])),
      ...findingBlocks(s.found, s.found.length === 1 ? "mentions it" : "mention it", 3),
      text(undefined, nextStep(s.found) || "No open finding mentions it."),
    ], s.lines.map((l) => l.material_id), [`Show ${s.lines[0].material_id}`, `Tell me about ${supName}`, ...follow]);
  };
  return { intent: "po_detail", workingTitle: `Tracing PO ${label}`, steps, compose };
}

function planShipment(id: string): PlannedRun {
  const s = { rows: [] as ShipmentRow[], lines: [] as PoLine[], sups: [] as SupplierRow[], found: [] as Issue[] };
  const steps = [
    step("shipment", "Found the shipment in the shipping feeds", ["truth store"], async () => {
      const [ships, sups] = await Promise.all([api.shipments(), api.suppliers()]);
      Object.assign(s, { sups, rows: shipmentGroups(ships.filter((x) => x.shipment_id === id)).get(id) ?? [] });
      return s.rows[0] ? `${shipWord(s.rows[0].status)} · ETA ${day(s.rows[0].eta)} · ${plural(s.rows.length, "feed")}` : "Not in the shipment files";
    }),
    step("lines", "Matched its PO lines", ["truth store"], async () => {
      s.lines = poLines((await api.journey()).filter((j) => j.shipment_id === id)).sort(byDue);
      return `${plural(s.lines.length, "PO line")} · ${fmt(sum(s.lines.map((l) => l.qty_ordered)))} pcs ordered`;
    }),
    step("findings", "Checked findings that mention it", ["Reconciler"], async () => {
      s.found = openFindings(await api.issues(), (i) => i.shipment_id === id || mentions(i, id));
      return plural(s.found.length, "open finding");
    }),
  ];
  const compose = (): AgentAnswer => {
    const title = `Traced ${id} across the shipping feeds and POs`;
    const follow = ["Do we have enough stock to finish the launch?", "Which parts are below 2 weeks cover?"];
    const main = s.rows[0];
    if (!main && !s.lines.length) return answer(title, [text(`I can't find ${id} in the shipment files.`, "No shipping feed or PO line Ingest has read mentions it.")], [], follow);
    const other = main && s.rows.find((x) => x.status && x.status !== main.status);
    const supId = main?.supplier_id || s.lines[0]?.supplier_id || "";
    const supName = s.sups.find((x) => x.supplier_id === supId)?.name || supId;
    const route = main
      ? [human(main.mode), main.origin && main.destination ? `from ${main.origin} to ${main.destination}` : "", main.via ? `via ${main.via}` : ""].filter(Boolean).join(" ")
      : "";
    const parts = uniq(s.lines.map((l) => l.material_id));
    return answer(title, [
      text(main ? `${id} is ${shipWord(main.status)}, ETA ${day(main.eta)}.` : `${id} carries ${plural(s.lines.length, "PO line")}, but no shipping feed lists it.`, sentences([
        main && `${cap(route) || "Route unknown"}${main.carrier_id ? `, carrier ${main.carrier_id}` : ""}${supName ? `, for ${supName}` : ""}.`,
        main?.note && `Note: ${trimDot(main.note)}.`,
        other && `${fileName(other.source)} says ${shipWord(other.status)} instead.`,
      ])),
      s.lines.length
        ? table([["PO line"], ["Part"], ["Line qty", "right"], ["Received", "right"]], s.lines.map((l) => [l.key, l.material_id, fmt(l.qty_ordered), fmt(l.qty_received)]))
        : text(undefined, "No PO line points at it."),
      ...findingBlocks(s.found, s.found.length === 1 ? "mentions it" : "mention it", 3),
      text(undefined, nextStep(s.found) || "No open finding mentions it."),
    ], parts, [parts[0] ? `Show ${parts[0]}` : "", supId ? `Tell me about ${supName}` : "", ...follow]);
  };
  return { intent: "shipment_detail", workingTitle: `Tracing ${id}`, steps, compose };
}

// ---------------------------------------------------------------------------
// Watcher: the newest drop, every drop, one file, removed files
// ---------------------------------------------------------------------------
function dropLead(d: DropRecord): string {
  const [a, c, r] = [d.added.length, d.changed.length, d.removed.length];
  const head = `Drop ${d.id} at ${dropTime(d)}`;
  if (!a && !c && !r) return `${head} found nothing new.`;
  const brought = [a ? plural(a, "new file") : "", c ? plural(c, "changed file") : "", r ? plural(r, "removed file") : ""];
  const none = [c ? "" : "changes", r ? "" : "removals"].filter(Boolean);
  return `${head} brought ${andList(brought)}${none.length ? `, no ${none.join(" or ")}` : ""}.`;
}

/** The newest drop, or the one the question names ("what did drop 2 bring?"). */
function planLatestDrop(dropId?: number): PlannedRun {
  const dropsP = once(api.drops);
  const pick = (drops: DropRecord[]) => (dropId ? drops.find((d) => d.id === dropId) : newestDrop(drops));
  const s = { drop: undefined as DropRecord | undefined, count: 0, truth: null as TruthData | null, changes: [] as FieldChange[], cited: 0 };
  const steps = [
    step("drops", "Checked the portal drops", ["portal"], async () => {
      const drops = await dropsP();
      Object.assign(s, { drop: pick(drops), count: drops.length });
      if (!s.drop) return dropId ? `${plural(drops.length, "drop")} · no Drop ${dropId}` : "No drops yet";
      return `${plural(drops.length, "drop")} · ${dropId ? "" : "newest "}${dropLabel(s.drop.id, s.drop.label)}`;
    }),
    step("truth", "Matched the files to the truth store", ["truth store"], async () => {
      const [truth, drops] = await Promise.all([api.truth(), dropsP()]);
      s.truth = truth;
      const d = pick(drops);
      if (!d) return "No drop to match";
      const paths = [...d.added, ...d.changed];
      const read = paths.filter((p) => truth.sources[p]);
      if (!read.length) return `No new rows from ${plural(paths.length, "file")}`;
      return `${plural(sum(read.map((p) => truth.sources[p].rows)), "row")} from ${fmt(read.length)} of ${plural(paths.length, "file")}`;
    }),
    step("changes", "Checked what Ingest changed from them", ["change log"], async () => {
      const [changes, issues, drops] = await Promise.all([api.changes(), api.issues(), dropsP()]);
      const d = pick(drops);
      if (!d) return "Nothing to check";
      const paths = new Set(dropPaths(d));
      s.changes = byIngest(changes).filter((c) => paths.has(sourceFile(c.source)));
      s.cited = openFindings(issues, (i) => citesFile(i, paths)).length;
      return `${plural(s.changes.length, "value")} changed · ${plural(s.cited, "open finding")} ${s.cited === 1 ? "cites" : "cite"} them`;
    }),
  ];
  const compose = (): AgentAnswer => {
    const title = dropId ? `Checked Drop ${dropId}` : "Checked the newest portal drop";
    const follow = ["How many drops have you seen?", "Were any files removed?"];
    const d = s.drop;
    if (!d && dropId && s.count) {
      return answer(title, [text(`There's no Drop ${dropId}.`, `The Watcher has seen ${plural(s.count, "drop")} so far.`)], [], ["What's new in the portal?", ...follow]);
    }
    if (!d) return answer(title, [text("No drops yet — the Watcher hasn't seen the portal change.", "As soon as a check finds new files, I'll list them here.")], [], follow);
    const entries = dropPaths(d).map((p) => ({ p, change: changeIn(d, p) ?? "added" }));
    const read = [...d.added, ...d.changed].filter((p) => s.truth?.sources[p]);
    const rows = sum(read.map((p) => s.truth?.sources[p]?.rows ?? 0));
    const tables = uniq(read.flatMap((p) => s.truth?.sources[p]?.tables ?? [])).map(human);
    const n = s.changes.length;
    const shown = entries.slice(0, 8);
    return answer(title, [
      text(dropLead(d), !entries.length ? undefined : rows
        ? `Ingest read ${plural(rows, "row")} from them into ${andList(tables)}${n ? ` and changed ${plural(n, "value")}` : ""}.`
        : n ? `Ingest added no new rows from them but changed ${plural(n, "value")} in ${andList(uniq(s.changes.map((c) => human(c.table_name))))}.`
          : "Ingest hasn't written anything from them yet."),
      shown.length > 0 && table([["File"], ["Folder"], ["Kind"], ["Change"]], shown.map(({ p, change }) => [fileName(p), fileFolder(p), fileKind(p), cell(change, CHANGE_TONE[change])])),
      entries.length > 0 && text(undefined, sentences([
        entries.length > shown.length && `Showing ${fmt(shown.length)} of ${fmt(entries.length)} files.`,
        s.cited ? `${plural(s.cited, "open finding")} already ${s.cited === 1 ? "cites" : "cite"} these files — ask me about any of them.` : "No open finding cites them yet.",
      ])),
    ], dropPaths(d), [entries[0] ? `Tell me about ${fileName(entries[0].p)}` : "", ...follow]);
  };
  return { intent: "latest_drop", workingTitle: dropId ? `Checking Drop ${dropId}` : "Checking the newest drop", steps, compose };
}

function planHistory(): PlannedRun {
  const s = { drops: [] as DropRecord[], own: [] as AgentLogEntry[], handoffs: [] as AgentLogEntry[] };
  const steps = [
    step("drops", "Checked the portal drops", ["portal"], async () => {
      s.drops = [...(await api.drops())].sort((a, b) => b.id - a.id);
      return `${plural(s.drops.length, "drop")} · ${fmt(sum(s.drops.map((d) => d.added.length)))} files added`;
    }),
    step("trace", "Read the Watcher's trace", ["trace"], async () => {
      // In replay mode the coordinator logs each drop it hands to the agents; use that when the Watcher logged nothing.
      const [own, coordinator] = await Promise.all([api.trace("watcher", 40), api.trace("coordinator", 200).catch(() => [])]);
      s.own = own.sort((a, b) => b.id - a.id);
      s.handoffs = coordinator.filter((e) => e.action === "drop").sort((a, b) => b.id - a.id);
      return s.own.length ? `${plural(s.own.length, "entry", "entries")} · last at ${clock(s.own[0].ts)}` : `No Watcher entries · ${plural(s.handoffs.length, "drop hand-off")}`;
    }),
  ];
  const compose = (): AgentAnswer => {
    const title = "Went through every portal drop";
    const drops = s.drops;
    if (!drops.length) {
      return answer(title, [text("No drops yet.", "The Watcher hasn't recorded a portal check.")], [], ["What's new in the portal?", "Any errors?"]);
    }
    const total = (k: "added" | "changed" | "removed") => sum(drops.map((d) => d[k].length));
    const count = (n: number, tone: CellTone) => cell(fmt(n), n ? tone : "muted");
    const last = drops[0];
    // A live Watcher logs every check, also the ones that found nothing (those create no drop).
    const check = s.own.find((e) => e.action === "check");
    const quiet = !!check && (parseTs(check.ts)?.getTime() ?? 0) > (parseTs(last.checked_at)?.getTime() ?? 0);
    const lastCheck = quiet && check
      ? `Last check at ${clock(check.ts)} found nothing new since Drop ${last.id}.`
      : `Last check at ${clock(last.checked_at)}${last.mode ? ` (${last.mode})` : ""}.`;
    const totals = `${fmt(total("added"))} files added, ${fmt(total("changed"))} changed, ${total("removed") ? fmt(total("removed")) : "none"} removed`;
    const trail = s.own.length
      ? `Its latest trace entries: ${s.own.slice(0, 3).map((e) => `${clock(e.ts)} ${e.detail || human(e.action)}`).join("; ")}.`
      : `The Watcher hasn't written trace entries of its own yet; the coordinator logged ${plural(s.handoffs.length, "drop hand-off")}`
        + `${s.handoffs[0] ? `, the last at ${clock(s.handoffs[0].ts)}` : ""}.`;
    return answer(title, [
      text(`The Watcher has seen ${plural(drops.length, "drop")}: ${totals}.`, lastCheck),
      table([["Drop"], ["Time"], ["Added", "right"], ["Changed", "right"], ["Removed", "right"]], drops.slice(0, 10).map((d) =>
        [`Drop ${d.id}`, dropTime(d), count(d.added.length, "ok"), count(d.changed.length, "warn"), count(d.removed.length, "stop")])),
      text(undefined, trail),
    ], dropPaths(last), ["What's new in the portal?", "Were any files removed?"]);
  };
  return { intent: "drop_history", workingTitle: "Going through every drop", steps, compose };
}

function planFile(lower: string, planned: string[]): PlannedRun {
  const s = { drops: [] as DropRecord[], paths: planned, truth: null as TruthData | null, issues: [] as Issue[], changes: [] as FieldChange[] };
  const pathsP = once(async () => {
    s.drops = [...(await api.drops())].sort((a, b) => a.id - b.id);
    const fresh = matchFiles(lower, allPaths(s.drops));
    if (fresh.length) s.paths = fresh;
    return s.paths;
  });
  const rowsOf = (p: string) => s.truth?.sources[p]?.rows ?? 0;
  const lastDrop = (p: string) => [...s.drops].reverse().find((d) => changeIn(d, p));
  const steps = [
    step("drops", "Found it in the portal drops", ["portal"], async () => {
      const paths = await pathsP();
      const d = paths[0] ? lastDrop(paths[0]) : undefined;
      if (paths.length > 1) return plural(paths.length, "matching file");
      return d ? `${dropLabel(d.id, d.label)} · ${changeIn(d, paths[0])} · ${fileFolder(paths[0])}` : "Not in any drop";
    }),
    step("truth", "Matched it to the truth store", ["truth store"], async () => {
      const [truth, paths] = await Promise.all([api.truth(), pathsP()]);
      s.truth = truth;
      const rows = sum(paths.map(rowsOf));
      if (!rows) return `No rows written from ${paths.length > 1 ? "them" : "it"}`;
      return `${plural(rows, "row")} → ${andList(uniq(paths.flatMap((p) => truth.sources[p]?.tables ?? [])).map(human))}`;
    }),
    step("findings", "Checked findings and changes that cite it", ["Reconciler", "change log"], async () => {
      const [issues, all, paths] = await Promise.all([api.issues(), api.changes(), pathsP()]);
      const changes = byIngest(all);
      Object.assign(s, { issues, changes });
      const set = new Set(paths);
      const cited = openFindings(issues, (i) => citesFile(i, set)).length;
      return `${plural(cited, "open finding")} · ${plural(changes.filter((c) => set.has(sourceFile(c.source))).length, "value change")}`;
    }),
  ];

  const compose = (): AgentAnswer => {
    const paths = s.paths;
    const first = paths[0];
    const follow = ["What's new in the portal?", "How many drops have you seen?", "Were any files removed?"];
    const title = paths.length > 1 ? "Looked up the matching files across drops and the truth store" : `Looked up ${fileName(first) || "the file"} across drops, truth store and findings`;
    if (!first) return answer(title, [text("I can't find that file in any drop.", "Try its exact name, like the ones in the table.")], [], follow);
    const cited = openFindings(s.issues, (i) => citesFile(i, new Set(paths)));
    if (paths.length > 1) {
      const changed = s.changes.filter((c) => paths.includes(sourceFile(c.source))).length;
      const rows = sum(paths.map(rowsOf));
      return answer(title, [
        text(`I found ${plural(paths.length, "file")} that match.`, rows
          ? `Ingest read ${plural(rows, "row")} from them${changed ? ` and changed ${plural(changed, "value")}` : ""}.`
          : `Ingest added no rows from them${changed ? ` but changed ${plural(changed, "value")}` : ""}.`),
        table([["File"], ["Folder"], ["Drop"], ["Change"], ["Rows read", "right"]], paths.slice(0, 8).map((p) => {
          const d = lastDrop(p);
          const change = d ? changeIn(d, p) : null;
          return [fileName(p), fileFolder(p), d ? `Drop ${d.id}` : "–", change ? cell(change, CHANGE_TONE[change]) : "–", fmt(rowsOf(p))];
        })),
        text(undefined, cited.length
          ? `${plural(cited.length, "open finding")} ${cited.length === 1 ? "cites" : "cite"} them; the most severe: ${cited[0].summary}`
          : "No open finding cites them."),
      ], paths, follow);
    }
    const history = s.drops.filter((d) => changeIn(d, first));
    const last = history[history.length - 1];
    const when = last ? `Drop ${last.id} at ${dropTime(last)}` : "";
    const change = last ? changeIn(last, first) : null;
    const fromFile = s.changes.filter((c) => sourceFile(c.source) === first).sort((a, b) => b.id - a.id);
    const kind = fileKind(first);
    const rows = rowsOf(first);
    const c = fromFile[0];
    const changedText = fromFile.length ? `changed ${plural(fromFile.length, "value")} from it` : "";
    const readText = rows
      ? `Ingest read ${plural(rows, "row")} from it into ${andList((s.truth?.sources[first]?.tables ?? []).map(human))}${changedText ? ` and ${changedText}` : ""}.`
      : changedText ? `Ingest ${changedText}.` : "Ingest hasn't written rows from it.";
    return answer(title, [
      text(!last ? `${fileName(first)} isn't in any drop.`
        : change === "removed" ? `${fileName(first)} was removed in ${when} — its rows stay as history.`
          : change === "changed" ? `${fileName(first)} was updated in ${when}.` : `${fileName(first)} arrived as a new file in ${when}.`, sentences([
        `It's ${/^[AEIOU]/i.test(kind) ? "an" : "a"} ${kind} file in ${fileFolder(first)}.`,
        readText,
        history.length > 1 && `History: ${history.map((d) => `${changeIn(d, first)} in Drop ${d.id}`).join(", ")}.`,
      ])),
      ...findingBlocks(cited, cited.length === 1 ? "cites it" : "cite it"),
      text(undefined, c
        ? `Latest change from it: ${human(c.field)} on ${c.entity_key}, ${oldNew(c.old_value, c.new_value, 30)}.`
        : cited.length ? "Ingest hasn't changed any existing values from it." : "No open finding cites it."),
    ], [first], follow);
  };
  return { intent: "file_detail", workingTitle: `Looking up ${fileName(planned[0]) || "the file"}`, steps, compose };
}

function planRemoved(): PlannedRun {
  const dropsP = once(api.drops);
  const s = { drops: [] as DropRecord[], kept: 0 };
  const removedOf = (drops: DropRecord[]) => drops.flatMap((d) => d.removed.map((p) => ({ p, d })));
  const steps = [
    step("drops", "Checked every drop for removed files", ["portal"], async () => {
      s.drops = [...(await dropsP())].sort((a, b) => b.id - a.id);
      return `${plural(s.drops.length, "drop")} · ${plural(removedOf(s.drops).length, "removed file")}`;
    }),
    step("truth", "Checked what the truth store keeps from them", ["truth store"], async () => {
      const gone = removedOf(await dropsP());
      if (!gone.length) return "Nothing to check";
      const truth = await api.truth();
      s.kept = sum(gone.map(({ p }) => truth.sources[p]?.rows ?? 0));
      return `${plural(s.kept, "row")} kept as history`;
    }),
  ];
  const compose = (): AgentAnswer => {
    const title = "Checked every drop for removed files";
    const follow = ["What's new in the portal?", "How many drops have you seen?"];
    const gone = removedOf(s.drops);
    if (!gone.length) {
      const [added, changed] = [sum(s.drops.map((d) => d.added.length)), sum(s.drops.map((d) => d.changed.length))];
      const seen = s.drops.length
        ? `Across ${plural(s.drops.length, "drop")} the Watcher saw ${fmt(added)} files added and ${fmt(changed)} changed.`
        : "No drops have been recorded yet.";
      return answer(title, [text("Nothing was removed — removed files stay as history.", seen)], [], follow);
    }
    return answer(title, [
      text(`${plural(gone.length, "file")} ${gone.length === 1 ? "was" : "were"} removed across ${plural(s.drops.length, "drop")}.`,
        `Removed files stay as history: Ingest still keeps ${plural(s.kept, "row")} from them.`),
      table([["File"], ["Folder"], ["Drop"]], gone.slice(0, 8).map(({ p, d }) => [fileName(p), fileFolder(p), dropLabel(d.id, d.label)])),
    ], gone.map((g) => g.p), follow);
  };
  return { intent: "removed", workingTitle: "Checking for removed files", steps, compose };
}

// ---------------------------------------------------------------------------
// Both agents: errors, and an honest fallback
// ---------------------------------------------------------------------------
function planErrors(ctx: BrainContext): PlannedRun {
  const agent = ctx.agent;
  const who = agent === "watcher" ? "the Watcher" : "Ingest";
  const s = { trace: [] as AgentLogEntry[], flagged: [] as Issue[] };
  const steps = [
    step("trace", `Read the ${agent === "watcher" ? "Watcher" : "Ingest"} trace`, ["trace"], async () => {
      s.trace = (await api.trace(agent, 500)).sort((a, b) => b.id - a.id);
      return `${plural(s.trace.length, "entry", "entries")} · ${plural(s.trace.filter((e) => e.action === "error").length, "error")}`;
    }),
    step("findings", `Checked findings ${who} raised`, ["findings"], async () => {
      s.flagged = openFindings(await api.issues(), (i) => i.raised_by === agent);
      return plural(s.flagged.length, "open finding");
    }),
  ];
  const compose = (): AgentAnswer => {
    const errors = s.trace.filter((e) => e.action === "error");
    const flagged: Block[] = s.flagged.length
      ? [text(undefined, `${cap(who)} did flag ${plural(s.flagged.length, "thing")} for a human:`), bullets(s.flagged.slice(0, 4).map((i) => i.summary))]
      : [];
    return answer("Checked the trace for errors", errors.length
      ? [
          text(`${cap(who)} hit ${plural(errors.length, "error")}.`, `The latest was at ${clock(errors[0].ts)}.`),
          table([["Time"], ["What happened"]], errors.slice(0, 6).map((e) => [clock(e.ts), clip(e.detail || human(e.action), 90)])),
          ...flagged,
        ]
      : [
          text(`No errors — ${who} ran clean.`, s.trace.length
            ? `I went through ${plural(s.trace.length, "trace entry", "trace entries")}; the newest is from ${clock(s.trace[0].ts)}.`
            : "It hasn't written any trace entries yet."),
          ...flagged,
        ], [], starterQuestions(ctx));
  };
  return { intent: "errors", workingTitle: "Checking the trace for errors", steps, compose };
}

const CAN_DO: Record<WorkspaceAgent, string> = {
  ingest: "I can tell you whether the launch is covered, look up any part, supplier, PO or shipment, list parts low on cover, show what I changed in the truth store, or check for errors.",
  watcher: "I can tell you what the newest drop brought, how many drops I've seen, what came of any portal file, or which files were removed.",
};

function planFallback(ctx: BrainContext): PlannedRun {
  const s = { offline: false, latest: undefined as AgentLogEntry | undefined };
  const steps = [
    step("health", "Checked the backend", ["api"], async () => {
      try {
        const h = await api.health();
        return `${h.status} · ${h.mockMode ? "mock mode" : "live mode"} · ${plural(h.agents?.length ?? 0, "agent")}`;
      } catch {
        s.offline = true;
        return "Backend not reachable";
      }
    }),
    step("trace", "Looked at my latest work", ["trace"], async () => {
      if (s.offline) return "Skipped";
      const trace = await api.trace(ctx.agent, 50).catch(() => []);
      s.latest = trace.sort((a, b) => b.id - a.id).find((e) => !/^handle:|^llm_call$/.test(e.action));
      return s.latest ? `${clock(s.latest.ts)} · ${human(s.latest.action)}` : "No trace entries yet";
    }),
  ];
  const compose = () => answer("Checked what I can answer", [
    text("I can't answer that one yet.", CAN_DO[ctx.agent]),
    s.offline
      ? text(undefined, "The backend isn't reachable right now, so live answers will fail until it's back.")
      : !!s.latest && text(undefined, `My latest action, at ${clock(s.latest.ts)}: ${clip(s.latest.detail || human(s.latest.action), 120)}.`),
  ], [], starterQuestions(ctx));
  return { intent: "fallback", workingTitle: "Reading your question", steps, compose };
}

// ---------------------------------------------------------------------------
// Entry points
// ---------------------------------------------------------------------------
function planIngest(q: string, lower: string, ctx: BrainContext): PlannedRun {
  const upper = q.toUpperCase();
  const truth = ctx.truth;
  const shipment = upper.match(SHIPMENT_RE)?.[0] ?? truth?.shipments.find((x) => x.shipment_id && upper.includes(x.shipment_id.toUpperCase()))?.shipment_id;
  if (shipment) return planShipment(shipment);
  const ten = q.match(TEN_DIGIT_RE);
  if (ten) {
    const lot = truth?.stock.find((l) => l.batch === ten[1]); // a batch number stands for its part
    return lot ? planPart(ctx, lot.material_id) : planPo(ten[1], ten[2] ? Number(ten[2]) : undefined);
  }
  const part = findPart(upper, truth);
  if (part) return planPart(ctx, part);
  const supplier = findSupplier(lower, upper, truth);
  if (supplier) return planSupplier(supplier, truth);
  if (ERRORS_RE.test(lower) && !/launch|stock|short|cover/.test(lower)) return planErrors(ctx);
  if (CHANGES_RE.test(lower) || (UPDATE_RE.test(lower) && !/\blaunch\b/.test(lower))) return planChanges(ctx, lower);
  if (LOW_RE.test(lower)) return planLowCover(ctx, threshold(lower));
  if (LAUNCH_RE.test(lower)) return planLaunchCover(ctx);
  return planFallback(ctx);
}

function planWatcher(lower: string, ctx: BrainContext): PlannedRun {
  const files = matchFiles(lower, allPaths(ctx.snap.drops ?? []));
  // A file name the snapshot doesn't know yet is looked up in the fresh drop list.
  if (files.length || FILE_RE.test(lower)) return planFile(lower, files);
  if (REMOVED_RE.test(lower)) return planRemoved();
  if (ERRORS_RE.test(lower)) return planErrors(ctx);
  const dropNo = lower.match(DROP_NO_RE)?.[1];
  if (dropNo) return planLatestDrop(Number(dropNo));
  if (HISTORY_RE.test(lower)) return planHistory();
  if (LATEST_RE.test(lower)) return planLatestDrop();
  return planFallback(ctx);
}

export function planRun(question: string, ctx: BrainContext): PlannedRun {
  const q = (question ?? "").trim();
  const lower = q.toLowerCase();
  let run: PlannedRun;
  try {
    run = ctx.agent === "watcher" ? planWatcher(lower, ctx) : planIngest(q, lower, ctx);
  } catch {
    run = planFallback(ctx); // an odd snapshot shape must not leave the chat stuck
  }
  const compose = run.compose;
  return {
    ...run,
    compose: () => {
      try {
        return compose();
      } catch (e) {
        const reason = e instanceof Error ? e.message : String(e);
        return answer("Couldn't finish the answer", [text("I loaded the data but couldn't put the answer together.", reason)], [], starterQuestions(ctx));
      }
    },
  };
}

/** 3–4 starter questions for the empty chat, built from the current data. */
export function starterQuestions(ctx: BrainContext): string[] {
  try {
    return starters(ctx);
  } catch {
    return ctx.agent === "watcher"
      ? ["What's new in the portal?", "How many drops have you seen?", "Were any files removed?"]
      : ["Do we have enough stock to finish the launch?", "Which parts are below 2 weeks cover?", "What did you change in the truth store?"];
  }
}

function starters(ctx: BrainContext): string[] {
  const snap = ctx.snap;
  if (ctx.agent === "watcher") {
    const d = newestDrop(snap?.drops ?? []);
    const file = d ? [...d.added, ...d.changed][0] : undefined;
    return ["What's new in the portal?", file ? `Tell me about ${fileName(file)}` : "", "How many drops have you seen?", "Were any files removed?"]
      .filter(Boolean).slice(0, 4);
  }
  const worst = newestForecasts(snap?.forecast ?? [])
    .filter((f) => (f.shortfall ?? 0) > 0 && f.limiting_part)
    .sort((a, b) => (b.shortfall ?? 0) - (a.shortfall ?? 0))[0];
  const risky = latestRisks(snap?.risk ?? [], "supplier").sort((a, b) => b.grade - a.grade)[0];
  const riskyName = risky ? ctx.truth?.suppliers.find((x) => x.supplier_id === risky.entity_id)?.name || risky.entity_id : "";
  return [
    "Do we have enough stock to finish the launch?",
    worst ? `Why is ${worst.limiting_part} holding back ${worst.edition}?` : riskyName ? `What's going on with ${riskyName}?` : "",
    "Which parts are below 2 weeks cover?",
    "What did you change in the truth store?",
  ].filter(Boolean).slice(0, 4);
}
