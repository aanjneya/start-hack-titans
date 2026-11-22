"use client";

// Data the Watcher + Ingest built (the backend's truth store), loaded once per data drop,
// plus the two tables the workspace shows: launch parts (Ingest) and portal files (Watcher).
import { useCallback, useEffect, useRef, useState } from "react";
import { getData, parseTs, type FieldChange, type Forecast, type Issue, type ObservabilitySnapshot } from "@/lib/api";

// ---------------------------------------------------------------------------
// Truth-store rows (only the fields the UI reads; the API returns more)
// ---------------------------------------------------------------------------
export interface MaterialRow {
  component_id: string;
  description: string;
  category: string;
  critical: boolean;
  drawing_rev: string;
  uom: string;
  source: string;
}

export interface StockLotRow {
  material_id: string;
  quantity: number;
  weeks_of_cover: number | null;
  safety_stock: number | null;
  batch: string;
  revision: string;
  site: string;
  snapshot_date: string;
  last_count: string | null;
  source: string;
}

/**
 * One row of the backend view `part_journey`: a PO line joined with its confirmations, shipments and goods
 * receipts. The view links shipments per PO (not per line) and repeats a line for every shipment record and
 * receipt, so never sum quantities over these rows: use `poLines()` first.
 */
export interface JourneyRow {
  po_number: string;
  po_line: number;
  supplier_id: string;
  material_id: string;
  qty_ordered: number | null;
  promised_date: string | null;
  po_status: string;
  po_source: string;
  qty_confirmed: number | null;
  confirmed_delivery: string | null;
  confirmed_revision: string;
  shipment_id: string | null;
  shipment_mode: string | null;
  shipment_eta: string | null;
  shipment_status: string | null;
  shipment_updated_at: string | null;
  shipment_source: string | null;
  gr_number: string | null;
  qty_received: number | null;
  gr_date: string | null;
  last_step: string;
}

export interface BomRow {
  parent_id: string;
  child_id: string;
  qty_per: number;
  valid_from: string | null;
  valid_to: string | null;
  source: string;
}

export interface SupplierRow {
  supplier_id: string;
  name: string;
  legal_name: string;
  country: string;
  city: string;
  language: string;
  contact_name: string;
  contact_email: string;
  qualification_status: string;
  status: string;
  category: string;
  currency: string;
  payment_terms: string;
  incoterm: string;
  source: string;
}

export interface ShipmentRow {
  shipment_id: string;
  supplier_id: string;
  status: string;
  mode: string;
  eta: string | null;
  etd: string | null;
  origin: string;
  destination: string;
  via: string;
  note: string;
  reference: string;
  carrier_id: string;
  updated_at: string | null;
  source: string;
}

export interface ProductRow {
  product_id: string;
  name: string;
  lifecycle: string;
  launch_date: string | null;
  day_one_qty: number | null;
  source: string;
}

export interface EngineeringChangeRow {
  change_id: string;
  parent_id: string;
  child_id: string;
  action: string;
  rev_old: string;
  rev_new: string;
  reason: string;
  effective_from: string | null;
  use_up_old_rev: boolean;
  source: string;
}

export interface ConfirmationRow {
  confirmation_id: string;
  po_number: string;
  po_line: number;
  material_id: string;
  supplier_id: string;
  quantity: number | null;
  delivery_date: string | null;
  revision: string;
  confidence: number | null;
  source: string;
}

/** One purchase-order line, de-duplicated from `part_journey`. */
export interface PoLine {
  /** "4500318735/2" */
  key: string;
  po_number: string;
  po_line: number;
  supplier_id: string;
  material_id: string;
  po_status: string;
  po_source: string;
  qty_ordered: number;
  qty_confirmed: number | null;
  /** Sum over the line's distinct goods receipts. */
  qty_received: number;
  /** Ordered minus received, never below 0. */
  open: number;
  promised_date: string | null;
  confirmed_delivery: string | null;
  confirmed_revision: string;
  /** Distinct shipments linked to the PO (the backend links shipments per PO, not per line). */
  shipments: { id: string; eta: string | null; status: string | null; mode: string | null; updated_at: string | null }[];
  /** Confirmed delivery, else promised date, else the earliest linked shipment ETA. */
  due: string | null;
  /** Furthest step reached: ordered → confirmed → shipped → received. */
  last_step: string;
}

export interface TruthData {
  materials: MaterialRow[];
  stock: StockLotRow[];
  /** Raw `part_journey` rows (repeat per shipment/receipt; see `poLines`). */
  journey: JourneyRow[];
  /** One row per PO line (set by `loadTruth`; else derive with `poLines(journey)`). Use this for quantities. */
  poLines?: PoLine[];
  bom: BomRow[];
  suppliers: SupplierRow[];
  shipments: ShipmentRow[];
  products: ProductRow[];
  engineeringChanges: EngineeringChangeRow[];
  confirmations: ConfirmationRow[];
  /**
   * Per source file (path without any "#sheet" suffix): rows Ingest wrote from it (`rows`), rows it changed
   * from it (`updated`, from the change log: an update keeps the row's original source), both together
   * (`touched`, distinct rows), and the tables involved.
   */
  sources: Record<string, { rows: number; updated: number; touched: number; tables: string[] }>;
  /** Row count per truth table. */
  tableCounts: Record<string, number>;
}

/** Truth tables the backend serves at /api/truth/{table}. */
export const TRUTH_TABLES = [
  "purchase_orders", "order_confirmations", "goods_receipts", "stock", "shipments", "materials", "bom",
  "products", "suppliers", "supplier_part_map", "engineering_changes", "packout", "launch_status",
] as const;

type TruthTable = (typeof TRUTH_TABLES)[number];
type AnyRow = Record<string, unknown>;

/** Path of the file a row came from, without the "#sheet1.xml" part Excel rows carry. */
export const sourceFile = (source: unknown): string => String(source ?? "").split("#")[0];

export function fetchTruthTable<T = AnyRow>(table: TruthTable): Promise<T[]> {
  return getData<T[]>(`/api/truth/${table}?limit=5000`);
}

export function fetchJourney(): Promise<JourneyRow[]> {
  return getData<JourneyRow[]>("/api/part-journey?limit=5000");
}

export async function loadTruth(): Promise<TruthData> {
  const [tables, journey, changes] = await Promise.all([
    Promise.all(TRUTH_TABLES.map((t) => fetchTruthTable(t).then((rows) => [t, rows] as const))),
    fetchJourney(),
    getData<FieldChange[]>("/api/changes?limit=5000").catch(() => [] as FieldChange[]),
  ]);
  const byTable = Object.fromEntries(tables) as Record<TruthTable, AnyRow[]>;
  const tableCounts: Record<string, number> = {};
  // Distinct "table:id" keys per file, for rows written from it and rows changed from it.
  const written = new Map<string, Set<string>>();
  const changed = new Map<string, Set<string>>();
  const tablesOf = new Map<string, Set<string>>();
  const note = (map: Map<string, Set<string>>, path: string, table: string, id: unknown) => {
    if (!path) return;
    (map.get(path) ?? map.set(path, new Set()).get(path)!).add(`${table}:${String(id)}`);
    (tablesOf.get(path) ?? tablesOf.set(path, new Set()).get(path)!).add(table);
  };
  for (const [table, rows] of tables) {
    tableCounts[table] = rows.length;
    for (const row of rows) note(written, sourceFile(row.source), table, row.id);
  }
  for (const c of changes) note(changed, sourceFile(c.source), c.table_name, c.row_id);

  const sources: TruthData["sources"] = {};
  for (const path of tablesOf.keys()) {
    const w = written.get(path) ?? new Set<string>();
    const u = changed.get(path) ?? new Set<string>();
    sources[path] = { rows: w.size, updated: u.size, touched: new Set([...w, ...u]).size, tables: [...tablesOf.get(path)!] };
  }
  return {
    materials: byTable.materials as unknown as MaterialRow[],
    stock: byTable.stock as unknown as StockLotRow[],
    journey,
    poLines: poLines(journey),
    bom: byTable.bom as unknown as BomRow[],
    suppliers: byTable.suppliers as unknown as SupplierRow[],
    shipments: byTable.shipments as unknown as ShipmentRow[],
    products: byTable.products as unknown as ProductRow[],
    engineeringChanges: byTable.engineering_changes as unknown as EngineeringChangeRow[],
    confirmations: byTable.order_confirmations as unknown as ConfirmationRow[],
    sources,
    tableCounts,
  };
}

/**
 * Loads the truth store once, then again whenever `version` changes (pass something that
 * changes with each data drop). Keeps the last good data while reloading.
 */
export function useTruthData(version: string, enabled = true) {
  const [data, setData] = useState<TruthData | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Starts true so the first paint shows the skeleton, not an "empty" message.
  const [loading, setLoading] = useState(enabled);
  const [nonce, setNonce] = useState(0);
  const seq = useRef(0);

  useEffect(() => {
    if (!enabled) return;
    const mine = ++seq.current;
    let cancelled = false;
    // Deferred so no state is set synchronously inside the effect.
    const t = setTimeout(() => {
      if (cancelled) return;
      setLoading(true);
      loadTruth()
        .then((next) => {
          if (cancelled || mine !== seq.current) return;
          setData(next);
          setError(null);
        })
        .catch((e: unknown) => {
          if (cancelled || mine !== seq.current) return;
          setError((e as Error).message || "Could not load the truth store");
        })
        .finally(() => {
          if (!cancelled && mine === seq.current) setLoading(false);
        });
    }, 0);
    return () => {
      cancelled = true;
      clearTimeout(t);
    };
  }, [version, enabled, nonce]);

  const reload = useCallback(() => setNonce((n) => n + 1), []);
  return { data, error, loading, reload };
}

// ---------------------------------------------------------------------------
// Shared helpers
// ---------------------------------------------------------------------------
const CLOSED_PO = new Set(["delivered", "cancelled", "canceled", "closed", "gl", "lo"]);

/** PO line still open (legacy SAP codes GL/LO count as delivered/cancelled). Works on raw rows and PoLines. */
export const isOpenLine = (j: { po_status: string }) => !CLOSED_PO.has((j.po_status || "").toLowerCase());

/** Open PO line with quantity still to receive. */
export const isOpenPoLine = (l: PoLine) => isOpenLine(l) && l.open > 0;

/** Raw-row helper: quantity not yet received. Sum over `poLines()` instead, raw rows repeat. */
export const openQty = (j: JourneyRow) => Math.max(0, (j.qty_ordered ?? 0) - (j.qty_received ?? 0));

/** Raw-row helper: confirmed delivery, else promised date, else the shipment ETA. */
export const dueDate = (j: JourneyRow): string | null =>
  j.confirmed_delivery || j.promised_date || j.shipment_eta || null;

const STEP_RANK: Record<string, number> = { ordered: 0, confirmed: 1, shipped: 2, received: 3 };

/** Collapse `part_journey` rows to one row per PO line (receipts summed once, shipments listed once). */
export function poLines(journey: JourneyRow[]): PoLine[] {
  const lines = new Map<string, { line: PoLine; receipts: Map<string, number> }>();
  for (const j of journey) {
    const key = `${j.po_number}/${j.po_line}`;
    let entry = lines.get(key);
    if (!entry) {
      entry = {
        line: {
          key,
          po_number: j.po_number,
          po_line: j.po_line,
          supplier_id: j.supplier_id,
          material_id: j.material_id,
          po_status: j.po_status,
          po_source: j.po_source,
          qty_ordered: j.qty_ordered ?? 0,
          qty_confirmed: j.qty_confirmed,
          qty_received: 0,
          open: 0,
          promised_date: j.promised_date,
          confirmed_delivery: j.confirmed_delivery,
          confirmed_revision: j.confirmed_revision ?? "",
          shipments: [],
          due: null,
          last_step: j.last_step,
        },
        receipts: new Map(),
      };
      lines.set(key, entry);
    }
    const { line, receipts } = entry;
    if (!line.confirmed_delivery && j.confirmed_delivery) {
      line.confirmed_delivery = j.confirmed_delivery;
      line.qty_confirmed = j.qty_confirmed;
      line.confirmed_revision = j.confirmed_revision ?? "";
    }
    if (j.gr_number) receipts.set(j.gr_number, j.qty_received ?? 0);
    if (j.shipment_id) {
      const known = line.shipments.find((s) => s.id === j.shipment_id);
      const seen = { id: j.shipment_id, eta: j.shipment_eta, status: j.shipment_status, mode: j.shipment_mode, updated_at: j.shipment_updated_at };
      if (!known) line.shipments.push(seen);
      else if ((j.shipment_updated_at ?? "") > (known.updated_at ?? "")) Object.assign(known, seen);
    }
    if ((STEP_RANK[j.last_step] ?? 0) > (STEP_RANK[line.last_step] ?? 0)) line.last_step = j.last_step;
  }
  return [...lines.values()].map(({ line, receipts }) => {
    const received = [...receipts.values()].reduce((sum, q) => sum + q, 0);
    const etas = line.shipments.map((s) => s.eta).filter((d): d is string => !!d).sort();
    return {
      ...line,
      qty_received: received,
      open: Math.max(0, line.qty_ordered - received),
      due: line.confirmed_delivery || line.promised_date || etas[0] || null,
    };
  });
}

/** Newest forecast per edition (the backend returns newest first). */
export function newestForecasts(forecast: Forecast[]): Forecast[] {
  const byEdition = new Map<string, Forecast>();
  for (const f of forecast) {
    const cur = byEdition.get(f.edition);
    if (!cur || f.id > cur.id) byEdition.set(f.edition, f);
  }
  return [...byEdition.values()].sort((a, b) => a.edition.localeCompare(b.edition));
}

/** Launch close as a Berlin calendar date (YYYY-MM-DD), from the newest forecast. */
export function closeDate(forecast: Forecast[]): string | null {
  const deadline = newestForecasts(forecast).find((f) => f.deadline)?.deadline;
  const d = parseTs(deadline);
  return d ? d.toLocaleDateString("en-CA", { timeZone: "Europe/Berlin" }) : null;
}

export type CoverTone = "red" | "amber" | "green" | "none";

/** Traffic light for weeks of cover: under 2 red, under 4 amber, else green. */
export function coverTone(weeks: number | null | undefined): CoverTone {
  if (weeks == null || Number.isNaN(weeks)) return "none";
  if (weeks < 2) return "red";
  if (weeks < 4) return "amber";
  return "green";
}

// ---------------------------------------------------------------------------
// Ingest table: launch parts (like an inventory-planning sheet)
// ---------------------------------------------------------------------------
export interface PartRow {
  id: string;
  description: string;
  critical: boolean;
  /** Drawing revision in the master data (e.g. "C"). */
  revision: string;
  /** Sum of all stock lots (ERP stock snapshot). */
  freeStock: number;
  lots: number;
  /** Open PO quantity due on or before the launch close date. */
  onPoByClose: number;
  /** All open PO quantity, whenever due. */
  openPoQty: number;
  openLines: number;
  /** Earliest due date among open lines. */
  nextDue: string | null;
  /** ERP stock snapshot field. */
  weeksCover: number | null;
  /** Units to expedite so every launch copy gets packed: forecast shortfall × qty per copy, where this part limits an edition. */
  aiOrder: number;
  /** Units the launch editions still need: copies still to pack × qty per copy. */
  needToClose: number;
  /** Launch editions that use the part. */
  editions: string[];
  openFindings: number;
  topFinding: string | null;
  topSeverity: number;
}

export function buildPartRows(truth: TruthData | null, snap: ObservabilitySnapshot): PartRow[] {
  if (!truth) return [];
  const forecasts = newestForecasts(snap.forecast);
  const close = closeDate(snap.forecast);
  const qtyPer = (parent: string, child: string) =>
    truth.bom.find((b) => b.parent_id === parent && b.child_id === child)?.qty_per ?? 1;

  const ai = new Map<string, number>();
  const need = new Map<string, number>();
  const editions = new Map<string, string[]>();
  for (const f of forecasts) {
    if ((f.shortfall ?? 0) > 0 && f.limiting_part) {
      ai.set(f.limiting_part, (ai.get(f.limiting_part) ?? 0) + (f.shortfall ?? 0) * qtyPer(f.product_id, f.limiting_part));
    }
    for (const b of truth.bom.filter((row) => row.parent_id === f.product_id)) {
      need.set(b.child_id, (need.get(b.child_id) ?? 0) + (f.still_to_pack ?? 0) * (b.qty_per || 1));
      const list = editions.get(b.child_id) ?? [];
      if (!list.includes(f.edition)) list.push(f.edition);
      editions.set(b.child_id, list);
    }
  }

  const openIssues = snap.issues.filter((i: Issue) => i.status !== "resolved");
  const openLines = (truth.poLines ?? poLines(truth.journey)).filter(isOpenPoLine);

  return truth.materials
    .map((m): PartRow => {
      const lots = truth.stock.filter((s) => s.material_id === m.component_id);
      const covers = lots.map((s) => s.weeks_of_cover).filter((w): w is number => w != null);
      const open = openLines.filter((l) => l.material_id === m.component_id);
      const dues = open.map((l) => l.due).filter((d): d is string => !!d).sort();
      const findings = openIssues
        .filter((i) => i.part_id === m.component_id)
        .sort((a, b) => b.severity - a.severity);
      return {
        id: m.component_id,
        description: m.description,
        critical: !!m.critical,
        revision: m.drawing_rev || "",
        freeStock: lots.reduce((sum, s) => sum + (s.quantity || 0), 0),
        lots: lots.length,
        onPoByClose: open
          .filter((l) => (close ? !!l.due && l.due <= close : false))
          .reduce((sum, l) => sum + l.open, 0),
        openPoQty: open.reduce((sum, l) => sum + l.open, 0),
        openLines: open.length,
        nextDue: dues[0] ?? null,
        weeksCover: covers.length ? Math.max(...covers) : null,
        aiOrder: Math.round(ai.get(m.component_id) ?? 0),
        needToClose: Math.round(need.get(m.component_id) ?? 0),
        editions: editions.get(m.component_id) ?? [],
        openFindings: findings.length,
        topFinding: findings[0]?.summary ?? null,
        topSeverity: findings[0]?.severity ?? 0,
      };
    })
    .sort((a, b) => (a.weeksCover ?? Infinity) - (b.weeksCover ?? Infinity) || a.id.localeCompare(b.id));
}

// ---------------------------------------------------------------------------
// Watcher table: every portal file seen in a drop
// ---------------------------------------------------------------------------
export type FileChange = "added" | "changed" | "removed";

export interface FileRow {
  path: string;
  name: string;
  folder: string;
  kind: string;
  dropId: number;
  /** "Drop 3 · 17:23" */
  dropLabel: string;
  checkedAt: string;
  change: FileChange;
  /** Distinct rows Ingest wrote or updated from this file (null until the truth store has loaded). */
  rowsRead: number | null;
  tables: string[];
}

const KIND_BY_EXT: Record<string, string> = {
  csv: "CSV", xlsx: "Excel", xls: "Excel", pdf: "PDF", jpg: "Scan", jpeg: "Scan", png: "Scan",
  json: "JSON", docx: "Word", doc: "Word", md: "Notes", txt: "Text", eml: "Email",
};

const SOONY_AREA: Record<string, string> = {
  erp: "Soony ERP", master: "Soony master data", context: "Soony context", receiving: "Soony receiving",
  logistics: "Soony logistics", news: "Soony news",
};

export function fileKind(path: string): string {
  const ext = path.split(".").pop()?.toLowerCase() ?? "";
  return KIND_BY_EXT[ext] ?? (ext ? ext.toUpperCase() : "File");
}

export function fileFolder(path: string): string {
  const parts = path.split("/");
  if (parts[0] === "suppliers" && parts[1]) {
    const m = parts[1].match(/^(SUP-\d+)_(.+)$/);
    return m ? `${m[1]} ${m[2].replaceAll("_", " ")}` : parts[1];
  }
  if (parts[0] === "soony" && parts[1]) return SOONY_AREA[parts[1]] ?? `Soony ${parts[1]}`;
  return parts.slice(0, -1).join("/") || "Portal";
}

/** "data: Lio check 2026-11-21T17-23 – …" → "Drop 3 · 17:23". */
export function dropLabel(id: number, label: string): string {
  const t = label.match(/T(\d{2})-(\d{2})/);
  return t ? `Drop ${id} · ${t[1]}:${t[2]}` : `Drop ${id}`;
}

const CHANGE_ORDER: Record<FileChange, number> = { removed: 0, changed: 1, added: 2 };

export function buildFileRows(truth: TruthData | null, snap: ObservabilitySnapshot): FileRow[] {
  const byPath = new Map<string, FileRow>();
  for (const d of [...snap.drops].sort((a, b) => a.id - b.id)) {
    const put = (path: string, change: FileChange) => {
      const src = truth?.sources[path];
      byPath.set(path, {
        path,
        name: path.split("/").pop() ?? path,
        folder: fileFolder(path),
        kind: fileKind(path),
        dropId: d.id,
        dropLabel: dropLabel(d.id, d.label),
        checkedAt: d.checked_at,
        change,
        rowsRead: truth ? (src?.touched ?? 0) : null,
        tables: src?.tables ?? [],
      });
    };
    d.added.forEach((p) => put(p, "added"));
    d.changed.forEach((p) => put(p, "changed"));
    d.removed.forEach((p) => put(p, "removed"));
  }
  return [...byPath.values()].sort(
    (a, b) => b.dropId - a.dropId || CHANGE_ORDER[a.change] - CHANGE_ORDER[b.change] || a.path.localeCompare(b.path),
  );
}
