"use client";

// Control room: what can stop the line, one tile per part or supplier. Plain words and one number per tile;
// "See details" opens the three best actions as 3D cards, each routed to its approver by amount.
import {
  ArrowLeft, ArrowRight, Boxes, Check, ChevronDown, ChevronRight, ClipboardCheck, Loader2, Mail, PackageSearch, ShieldCheck, Sparkles, Truck,
  Users, X, type LucideIcon,
} from "lucide-react";
import Link from "next/link";
import ModalPanel from "./modal-panel";
import { findingHref, followupHref } from "./buyer-links";
import { SourceList } from "./decision-source";
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { TiltCard } from "@/components/ui/3d-card";
import { fmt, getData, postData, type DecisionCard, type Forecast, type Issue, type RiskScore } from "@/lib/api";
import { cn } from "@/lib/utils";
import type { ActionRun, BuyerQueueEntry, CustomerSetup } from "../../API_CONTRACT";

type Matrix = CustomerSetup["approval_matrix"];

export interface RiskAction {
  key: string;
  title: string;
  /** Which agent proposed it: "Planner" for decision-card options, else the agent that suggested the fix. */
  source: string;
  /** Option kind or suggested-action type (expedite, email, second_source, human review, …). */
  type: string;
  costEur: number | null;
  copiesGained: number | null;
  approvalValue: number | null;
  /** Approver roles from the approval matrix for this amount. */
  roles: string[];
  /** Named people assigned to approve. */
  people: string[];
  recommended: boolean;
  /** Who acts on a suggested fix: chaser / planner / human. */
  owner: string;
  /** A Launch Planner option the approve endpoint can execute. */
  card?: DecisionCard;
  optionIndex?: number;
  /** From the backend's buyer queue: sent with Approve; a changed option is refused (409) and the data refreshes. */
  version?: string;
}

export interface RiskGroup {
  /** Part or supplier id, e.g. CMP-GAM-010 or SUP-0029 (shown small, for traceability). */
  key: string;
  kind: "part" | "supplier" | "other";
  /** Plain-language title, e.g. "Wrong revision in stock". */
  title: string;
  /** What it is (part name), empty for a supplier. */
  thing: string;
  /** Who: the supplier's name. */
  company: string;
  severity: number;
  /** Stock cover in packing hours from the findings. */
  hoursLeft: number | null;
  /** Editions whose current forecast identifies this part as the binding constraint. */
  constrained: Forecast[];
  copiesAtRisk: number | null;
  futureSupply: { orderLines: number; leadDays: number | null } | null;
  editions: { edition: string; shortfall: number | null }[];
  rating: string | null;
  /** The finding that best explains the risk (its one-line summary is the executive summary). */
  lead: Issue | undefined;
  cards: DecisionCard[];
  findings: Issue[];
  /** Every candidate action, best first (the dialog shows three). */
  actions: RiskAction[];
}

const AGENT_NAME: Record<string, string> = {
  planner: "Planner", reconciler: "Reconciler", risk: "Risk Rater", ingest: "Ingest", chaser: "Chaser", watcher: "Watcher",
};

const ID_RE = /^(CMP-[A-Z]+-\d+|SUP-\d+)$/;
const norm = (s: string) => s.trim().toLowerCase().replace(/\s+/g, " ");

/** Content words two labels share ("pickup" in "Dispatch last-mile pickup …" and "Arrange terminal pickup …"); ids don't count. */
function sharedWords(a: string, b: string): number {
  const words = (s: string) =>
    new Set(
      s
        .toLowerCase()
        .split(/[^a-z-]+/)
        .map((w) => w.replace(/^-+|-+$/g, ""))
        .filter((w) => w.length >= 4 && !/^(with|from|for|the|and|into|this|that)$/.test(w)),
    );
  const left = words(a);
  return [...words(b)].filter((w) => left.has(w)).length;
}

/** Finding type -> what a person on the floor would call it. */
const TYPE_TITLE: Record<string, string> = {
  rev_blocked: "Wrong revision in stock",
  tracker_mismatch: "Shipment status doesn't match",
  stale_feed: "Tracking has gone quiet",
  missing_from_tracker: "Shipment missing from tracker",
  single_source_export_ban: "Export ban on the only supplier",
  single_source_export_risk: "Only supplier at export risk",
  single_source: "Only one supplier",
  po_confirmation_mismatch: "Supplier changed the order",
  po_stuck_approval: "Order stuck in approval",
  unconfirmed_po: "Order not confirmed",
  quality_hold_failed_aql: "Failed quality check",
  quantity_jump: "Odd delivery quantity",
  short_shipment: "Short delivery",
  supplier_downtime: "Supplier downtime",
  forecast_exposure: "Not enough for the launch",
  shipment_late_for_launch: "Shipment arrives too late",
  supplier_qualification_expired: "Supplier approval expired",
  second_source_candidates: "Needs a second supplier",
  bank_change: "Bank details changed",
  scans_unread: "Delivery scans not read yet",
  unmapped_files: "Files not understood yet",
};

/** Broad labels: shown only when nothing more specific explains the risk. */
const GENERIC_TYPES = new Set(["high_risk_rating", "missing_from_tracker", "scans_unread", "unmapped_files", "second_source_candidates"]);

/** Most severe first; at equal severity the specific reason before a broad rating; then newest. */
const byImportance = (a: Issue, b: Issue) =>
  b.severity - a.severity || Number(GENERIC_TYPES.has(a.type)) - Number(GENERIC_TYPES.has(b.type)) || b.id - a.id;

/** A rating finding only says "high risk"; its first driver says why. Plain words for the drivers the Risk rater writes. */
const DRIVER_TITLE: [RegExp, string][] = [
  [/export[- ]licen[cs]e|export (ban|control)/i, "Export licence notice"],
  [/suspension/i, "Supply may be suspended"],
  [/quality release expired|qualification\b.*\bexpired/i, "Quality release expired"],
  [/single[- ]source/i, "Only one supplier"],
  [/zero (warehouse )?stock/i, "No stock left"],
  [/status '?watch\b/i, "Supplier on watch list"],
  [/status '?at risk\b/i, "Supplier at risk"],
];

/** "Risk index 91/100 (CCC). Top drivers: <first driver> (+30pts) | ..." -> the first driver. */
const firstDriver = (top: Issue) => /Top drivers:\s*(.*?)(?:\s*\(\+[\d.]+\s*pts\)|\s*\||$)/.exec(top.reason ?? "")?.[1] ?? top.summary ?? "";

function riskTitle(top: Issue | undefined, kind: RiskGroup["kind"]): string {
  if (!top) return kind === "supplier" ? "Supplier needs attention" : "Part needs attention";
  if (top.type === "high_risk_rating") {
    const driver = firstDriver(top);
    return DRIVER_TITLE.find(([re]) => re.test(driver))?.[1] ?? (kind === "supplier" ? "High-risk supplier" : "High-risk part");
  }
  const known = TYPE_TITLE[top.type];
  if (known) return known;
  const words = top.type.replaceAll("_", " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
}

/** "Cover Sleeve Grand Heist 6 (USK/PEGI)" -> "Cover Sleeve Grand Heist 6"; "Game Card 128 GB – Grand Heist 6" -> "Game Card 128 GB". */
const shortName = (s: string) => s.replace(/\s*\([^)]*\)/g, "").replace(/\s+[–—]\s+.*$/, "").trim();

const approvalCurrency = new Intl.NumberFormat("en-IE", {
  style: "currency", currency: "EUR", minimumFractionDigits: 0, maximumFractionDigits: 0,
});
const money = (v: number | null) => (v == null ? "–" : approvalCurrency.format(v));

/** The approval band an amount falls in. */
function band(value: number | null, matrix: Matrix) {
  if (value == null) return null;
  const i = matrix.findIndex((r) => r.below_eur == null || value < r.below_eur);
  return i < 0 ? null : { rule: matrix[i], lower: matrix[i - 1]?.below_eur ?? 0 };
}

/** The part or supplier a decision card is about: the planner's title ends with it ("Standard · rev blocked · CMP-GAM-004"). */
function subjectOf(card: DecisionCard, issueById: Map<number, Issue>): string {
  const last = card.title.split(" · ").pop()?.trim() ?? "";
  if (ID_RE.test(last)) return last;
  for (const id of card.issue_ids) {
    const i = issueById.get(id);
    if (i?.part_id) return i.part_id;
  }
  for (const id of card.issue_ids) {
    const i = issueById.get(id);
    if (i?.supplier_id) return i.supplier_id;
  }
  return last || card.title;
}

/** Part and supplier ids named inside a sentence. */
const NAMED_ID_RE = /\b(?:CMP-[A-Z]+-\d+|SUP-\d+)\b/g;

/** The ids an action may name and still be about this tile: the part or supplier itself and, as the findings link
 *  them, its suppliers (part tile) or its parts (supplier tile). */
function actionScope(key: string, kind: RiskGroup["kind"], issues: Issue[]): { key: string; related: Set<string> } | null {
  if (kind === "other") return null;
  const related = new Set([key]);
  for (const i of issues) {
    if (i.status === "resolved") continue;
    if (kind === "part" && i.part_id === key && i.supplier_id) related.add(i.supplier_id);
    if (kind === "supplier" && i.supplier_id === key && i.part_id) related.add(i.part_id);
  }
  return { key, related };
}

/** The Planner adds the edition's bottleneck fix ("Review recovery options for CMP-GAM-010") to every card of that
 *  edition. On another tile it is not one of the best actions. A named part decides (it must be this tile's part, or
 *  one of this supplier's parts); a supplier only counts when no part is named, and never for a fix from this tile's own
 *  finding, which may name the alternative supplier to use ("Evaluate rush rev C run via SUP-0028"). */
function aboutOther(title: string, scope: { key: string; related: Set<string> } | null, ownFix: boolean): boolean {
  if (!scope) return false;
  const ids = title.match(NAMED_ID_RE) ?? [];
  const parts = ids.filter((id) => id.startsWith("CMP-"));
  if (parts.length) return !parts.some((id) => scope.related.has(id));
  if (ownFix) return false;
  return ids.length > 0 && !ids.some((id) => scope.related.has(id));
}

/** This tile's actions from the buyer queue, in the queue's order: the part's own actions on a part tile; the supplier's
 *  actions (or actions on its parts that name no supplier) on a supplier tile; otherwise actions backed by its cards. */
function queueActions(
  queue: BuyerQueueEntry[],
  group: { key: string; kind: RiskGroup["kind"]; cards: DecisionCard[] },
  scope: { key: string; related: Set<string> } | null,
  cardById: Map<number, DecisionCard>,
  matrix: Matrix,
): RiskAction[] {
  const own = new Set(group.cards.map((c) => c.id));
  return queue.flatMap((q): RiskAction[] => {
    const supplier = q.option.actions?.[0]?.supplier_id;
    const mine =
      group.kind === "part"
        ? q.part === group.key
        : group.kind === "supplier"
          ? supplier === group.key || (!supplier && !!q.part && !!scope?.related.has(q.part))
          : q.sources.some((src) => own.has(src.decision_id));
    const card = cardById.get(q.decision_id);
    if (!mine || !card || q.option.followup_for != null) return [];
    const o = q.option;
    const value = o.approval_value_eur ?? null;
    const roles = o.approval_roles ?? band(value, matrix)?.rule.approvers ?? [];
    return [{
      key: `queue:${q.key}`,
      title: o.label,
      source: "Planner",
      type: o.description || "option",
      costEur: o.cost_eur ?? null,
      copiesGained: o.copies_gained ?? null,
      approvalValue: value,
      roles,
      people: (o.approvers ?? []).filter((p) => !roles.includes(p)),
      recommended: false,
      owner: "",
      card,
      optionIndex: q.option_index,
      version: q.version,
    }];
  });
}

/**
 * Best actions first: priced Planner options (they gain copies or cost money; the recommended one first), then the
 * fixes other agents suggest that gain copies, then the remaining executable Planner options, then other suggested
 * fixes, and last the Planner options that still lack evidence. "Keep the current plan" is never an action.
 * With a scope, actions about another part or supplier (aboutOther) come after this tile's own.
 */
function rankActions(
  cards: DecisionCard[],
  findings: Issue[],
  matrix: Matrix,
  scope: { key: string; related: Set<string> } | null = null,
): RiskAction[] {
  type Entry = { action: RiskAction; tier: number; severity: number; ownFix?: boolean };
  const ranked: Entry[] = [];
  const options: { entry: Entry; card: DecisionCard; option: DecisionCard["options"][number] }[] = [];
  for (const card of cards) {
    card.options.forEach((o, i) => {
      if (o.description === "baseline") return;
      const value = o.approval_value_eur ?? null;
      const roles = o.approval_roles ?? band(value, matrix)?.rule.approvers ?? [];
      const available = o.available !== false;
      const priced = (o.copies_gained ?? 0) > 0 || (o.cost_eur ?? 0) > 0;
      const entry: Entry = {
        tier: !available ? 4 : priced ? 0 : 2,
        severity: 0,
        action: {
          key: `opt:${card.id}:${i}`,
          title: o.label,
          source: "Planner",
          type: o.description || "option",
          costEur: o.cost_eur,
          copiesGained: o.copies_gained,
          approvalValue: value,
          roles,
          people: (o.approvers ?? []).filter((p) => !roles.includes(p)),
          recommended: card.recommended === i,
          owner: "",
          card,
          optionIndex: i,
        },
      };
      ranked.push(entry);
      options.push({ entry, card, option: o });
    });
  }
  for (const f of findings) {
    for (const [n, a] of (f.suggested_actions ?? []).entries()) {
      if (!a.label) continue;
      const tier = (a.copies_gained ?? 0) > 0 ? 1 : 3;
      const ownFix = !!scope && (f.part_id === scope.key || f.subject_id === scope.key);
      // The Planner may already have turned this fix into an option it can execute: same finding, same shipment or
      // PO. Then show that option (it has an Approve button) at the fix's rank instead of the fix.
      // Several options can touch the same shipment (confirm it, pick it up): take the one worded most like the fix.
      const physical = /^(SHP-|\d{10})/.test(a.target ?? "");
      const twin = physical
        ? options
            .filter(
              ({ card, option }) =>
                option.available !== false &&
                card.issue_ids.includes(f.id) &&
                (option.actions ?? []).some((step) => Object.values(step).some((v) => v != null && String(v) === a.target)),
            )
            .map((c) => ({ ...c, overlap: sharedWords(c.option.label, a.label) }))
            .filter((c) => c.overlap > 0)
            .sort((x, y) => y.overlap - x.overlap)[0]
        : undefined;
      if (twin) {
        twin.entry.tier = Math.min(twin.entry.tier, tier);
        twin.entry.severity = Math.max(twin.entry.severity, f.severity);
        twin.entry.ownFix ||= ownFix;
        continue;
      }
      ranked.push({
        tier,
        severity: f.severity,
        ownFix,
        action: {
          key: `fix:${f.id}:${n}`,
          title: a.label,
          source: AGENT_NAME[f.raised_by] ?? f.raised_by,
          type: a.type,
          costEur: a.cost_eur,
          copiesGained: a.copies_gained,
          approvalValue: a.cost_eur,
          roles: band(a.cost_eur, matrix)?.rule.approvers ?? [],
          people: [],
          recommended: false,
          owner: a.owner,
        },
      });
    }
  }
  // The same action can come twice (the Planner copies a finding's fix word for word): keep one entry, preferring
  // the Planner option because it can be approved, with the better rank and any copies estimate the twin carries.
  const executable = (a: RiskAction) =>
    !!a.card && a.optionIndex != null && a.card.options[a.optionIndex]?.available !== false;
  const byTitle = new Map<string, Entry>();
  for (const r of ranked) {
    const k = norm(r.action.title);
    const cur = byTitle.get(k);
    if (!cur) {
      byTitle.set(k, { ...r });
      continue;
    }
    const [keep, other] = !executable(cur.action) && executable(r.action) ? [r, cur] : [cur, r];
    byTitle.set(k, {
      tier: Math.min(cur.tier, r.tier),
      severity: Math.max(cur.severity, r.severity),
      ownFix: cur.ownFix || r.ownFix,
      action: {
        ...keep.action,
        copiesGained: keep.action.copiesGained ?? other.action.copiesGained,
        recommended: keep.action.recommended || other.action.recommended,
      },
    });
  }
  return [...byTitle.values()]
    .map((r) => (aboutOther(r.action.title, scope, !!r.ownFix) ? { ...r, tier: Math.max(r.tier, 3.5) } : r))
    .sort(
      (x, y) =>
        x.tier - y.tier ||
        Number(y.action.recommended) - Number(x.action.recommended) ||
        (y.action.copiesGained ?? 0) - (x.action.copiesGained ?? 0) ||
        y.severity - x.severity ||
        (x.action.costEur ?? Infinity) - (y.action.costEur ?? Infinity),
    )
    .map((r) => r.action);
}

function futureSupplyImpact(actions: RiskAction[]): RiskGroup["futureSupply"] {
  const exposures = actions.flatMap(action => {
    const option = action.optionIndex == null ? undefined : action.card?.options[action.optionIndex];
    return option?.horizon === "future" && option.exposure ? [option.exposure] : [];
  });
  if (!exposures.length) return null;
  const lines = new Set<string>();
  const leadTimes = new Set<number>();
  for (const exposure of exposures) {
    for (const order of exposure.orders ?? []) {
      if (order.po && order.line != null) lines.add(JSON.stringify([order.po, order.line]));
    }
    if (exposure.lead_days != null) leadTimes.add(exposure.lead_days);
  }
  return { orderLines: lines.size, leadDays: leadTimes.size === 1 ? [...leadTimes][0] : null };
}

export function buildRiskGroups(input: {
  decisions: DecisionCard[];
  issues: Issue[];
  risk: RiskScore[];
  forecast: Forecast[];
  matrix: Matrix;
  names: Record<string, string>;
  /** The backend's ranked buyer queue; null when the backend has none (then the tiles rank the actions themselves). */
  queue?: BuyerQueueEntry[] | null;
}): RiskGroup[] {
  const { decisions, issues, risk, forecast, matrix, names, queue = null } = input;
  const issueById = new Map(issues.map((i) => [i.id, i]));
  const cardById = new Map(decisions.map((d) => [d.id, d]));
  const shortfall = new Map<string, number | null>();
  for (const f of forecast) if (!shortfall.has(f.edition)) shortfall.set(f.edition, f.shortfall);

  const bySubject = new Map<string, DecisionCard[]>();
  for (const d of decisions) {
    if (d.status !== "proposed") continue;
    const key = subjectOf(d, issueById);
    bySubject.set(key, [...(bySubject.get(key) ?? []), d]);
  }

  const latestRisk = new Map<string, RiskScore>();
  for (const r of risk) if ((latestRisk.get(r.entity_id)?.id ?? -1) < r.id) latestRisk.set(r.entity_id, r);

  const groups: RiskGroup[] = [...bySubject.entries()].map(([key, cards]) => {
    // The findings behind this subject's cards say what the risk is; other open findings on it add numbers.
    const behindCards = new Map<number, Issue>();
    for (const c of cards) for (const id of c.issue_ids) {
      const i = issueById.get(id);
      if (i && i.status !== "resolved") behindCards.set(i.id, i);
    }
    const kind: RiskGroup["kind"] = key.startsWith("CMP-") ? "part" : key.startsWith("SUP-") ? "supplier" : "other";
    // A supplier tile takes findings about the supplier itself, not every finding on parts it supplies
    // (those have their own tiles and would only repeat their numbers here).
    const linked = new Map(behindCards);
    for (const i of issues) {
      if (i.status === "resolved") continue;
      const about =
        kind === "supplier"
          ? (i.subject_type === "supplier" && i.subject_id === key) || (!i.part_id && i.supplier_id === key)
          : i.part_id === key || i.subject_id === key;
      if (about) linked.set(i.id, i);
    }
    const reasons = [...behindCards.values()].sort(byImportance);
    const findings = [...linked.values()].sort(byImportance);
    const top = reasons[0] ?? findings[0];
    const supplierId = kind === "supplier" ? key : (top?.supplier_id || findings.find((f) => f.supplier_id)?.supplier_id);
    const company = supplierId ? names[supplierId] ?? supplierId : "";
    const thing = kind === "part" ? shortName(names[key] ?? key) : "";
    const numbers = (pick: (f: Issue) => number | null | undefined) => {
      const own = reasons.map(pick).filter((n): n is number => n != null);
      return own.length ? own : findings.map(pick).filter((n): n is number => n != null);
    };
    const hours = numbers((f) => ("hours_buffer" in f.impact ? f.impact.hours_buffer : null));
    const copies = numbers((f) => ("copies_at_risk" in f.impact ? f.impact.copies_at_risk : null)).filter((c) => c > 0);
    const hoursLeft = hours.length ? Math.min(...hours) : null;
    const editions = [...new Set(cards.map((c) => c.title.split(" · ")[0]).filter((e) => shortfall.has(e)))].map(
      (edition) => ({ edition, shortfall: shortfall.get(edition) ?? null }),
    );
    const scope = actionScope(key, kind, issues);
    // The buyer queue (backend ranking) wins where it has actions for this tile; otherwise rank the cards' options here.
    const queued = queue ? queueActions(queue, { key, kind, cards }, scope, cardById, matrix) : [];
    const actions = queued.length ? queued : rankActions(cards, findings, matrix, scope);
    const constrained = forecast.filter(f => f.limiting_part === key && (f.shortfall ?? 0) > 0);
    const hasCurrentImpact = constrained.length > 0 || copies.length > 0 || actions.some(a => (a.copiesGained ?? 0) > 0);
    const futureSupply = hasCurrentImpact ? null : futureSupplyImpact(actions);
    return {
      key,
      kind,
      title: riskTitle(top, kind),
      thing,
      company,
      severity: top?.severity ?? 4,
      hoursLeft,
      constrained,
      copiesAtRisk: copies.length ? Math.max(...copies) : null,
      futureSupply,
      editions,
      rating: latestRisk.get(key)?.rating ?? null,
      lead: top,
      cards,
      findings,
      actions,
    };
  });

  const urgency = (g: RiskGroup) => (g.hoursLeft == null ? Infinity : g.hoursLeft);
  return groups.sort((a, b) => Number(!!b.constrained.length) - Number(!!a.constrained.length)
    || b.severity - a.severity || urgency(a) - urgency(b) || a.key.localeCompare(b.key));
}

/** Stock cover is evidence about one part. Only the forecast identifies the constraint on finished copies. */
function headline(g: RiskGroup): { value: string; caption: string; stop: boolean } | null {
  const binding = g.constrained[0];
  if (binding?.shortfall != null) return { value: fmt(binding.shortfall), caption: `${binding.edition} copies short`, stop: true };
  if (g.copiesAtRisk != null) return { value: fmt(g.copiesAtRisk), caption: "copies at risk", stop: true };
  if (g.futureSupply) {
    const { orderLines, leadDays } = g.futureSupply;
    if (orderLines) return { value: fmt(orderLines), caption: `future order ${orderLines === 1 ? "line" : "lines"} to review`, stop: false };
    if (leadDays != null) return { value: `${fmt(leadDays)} days`, caption: "lead time for future deliveries", stop: false };
    return { value: "Future supply", caption: "exposure to verify", stop: false };
  }
  if (g.hoursLeft != null) return { value: `${fmt(Math.max(0, g.hoursLeft))} h`, caption: "of stock at the packing rate", stop: false };
  return null;
}

// ---------------------------------------------------------------------------
// Board
// ---------------------------------------------------------------------------
export function RiskBoard({
  decisions,
  issues,
  risk,
  forecast,
  setup,
  queue,
  focus,
  emptyText,
  onChanged,
  onActionClose,
  current,
  renderDecision,
  renderAction,
}: {
  decisions: DecisionCard[];
  issues: Issue[];
  risk: RiskScore[];
  forecast: Forecast[];
  setup: CustomerSetup | null;
  /** The backend's ranked buyer queue (GET /api/buyer-queue, loaded with the cards); null when the backend has none,
   *  and the tiles then rank the actions themselves. */
  queue: BuyerQueueEntry[] | null;
  /** Open this exact decision from the buyer queue, even when its risk tile is folded away. */
  focus?: { key: string; n: number } | null;
  emptyText: string;
  onChanged: () => void;
  onActionClose?: () => void;
  current: boolean;
  /** The full decision card, for "More details" inside the dialog. */
  renderDecision?: (card: DecisionCard) => ReactNode;
  /** The full view of one buyer-queue action (its key), for "More details"; cards it covers are not repeated. */
  renderAction?: (key: string) => ReactNode;
}) {
  const [names, setNames] = useState<Record<string, string>>({});
  const [showAll, setShowAll] = useState(false);
  const [snapshot, setSnapshot] = useState<RiskGroup | null>(null);
  const needNames = Object.keys(names).length === 0;

  // Part descriptions and supplier names (best effort; tiles fall back to ids). Retried with each data refresh
  // until they arrive, in case the backend was down when the page loaded.
  useEffect(() => {
    if (!needNames) return;
    let alive = true;
    Promise.all([
      getData<{ component_id: string; description: string }[]>("/api/truth/materials").catch(() => []),
      getData<{ supplier_id: string; name: string }[]>("/api/truth/suppliers").catch(() => []),
    ]).then(([materials, suppliers]) => {
      if (!alive || (!materials.length && !suppliers.length)) return;
      setNames(
        Object.fromEntries([
          ...materials.map((m) => [m.component_id, m.description] as const),
          ...suppliers.map((s) => [s.supplier_id, s.name] as const),
        ]),
      );
    });
    return () => {
      alive = false;
    };
  }, [needNames, decisions]);

  const matrix = useMemo(() => setup?.approval_matrix ?? [], [setup]);
  const groups = useMemo(
    () => buildRiskGroups({ decisions, issues, risk, forecast, matrix, names, queue }),
    [decisions, issues, risk, forecast, matrix, names, queue],
  );
  const [selectedAction, setSelectedAction] = useState<string | null>(null);
  const close = useCallback(() => {
    setSnapshot(null);
    setSelectedAction(null);
  }, []);
  const closeAction = useCallback(() => {
    close();
    onActionClose?.();
  }, [close, onActionClose]);
  const [focusSeen, setFocusSeen] = useState(0);
  if (focus && focus.n !== focusSeen) {
    setFocusSeen(focus.n);
    setSnapshot(null);
    setSelectedAction(focus.key);
  }

  return (
    <section aria-labelledby="decide">
      <div className="mb-3 flex items-baseline justify-between gap-4">
        <h2 id="decide" className="text-xl font-bold tracking-tight">
          Risks to act on
        </h2>
        {groups.length > 3 && (
          <button type="button" aria-expanded={showAll} aria-controls="risk-list"
            className="shrink-0 rounded px-1 py-1 text-sm font-semibold text-act hover:underline"
            onClick={() => setShowAll(!showAll)}>
            {showAll ? "Show fewer risks" : `View all ${groups.length} risks`}
          </button>
        )}
      </div>

      {groups.length === 0 ? (
        <p className="rounded-lg border border-dashed border-rule bg-surface px-5 py-8 text-muted">{emptyText}</p>
      ) : (
        <ol id="risk-list" className="divide-y divide-rule overflow-hidden rounded-lg border border-rule bg-surface">
          {(showAll ? groups : groups.slice(0, 3)).map((g) => (
            <li key={g.key}>
              <RiskRow group={g} onOpen={() => setSnapshot(g)} />
            </li>
          ))}
        </ol>
      )}

      {selectedAction && renderAction && (
        <ModalPanel label="Buyer decision" onClose={closeAction}>
          <div className="p-4 sm:p-6">
            <div className="mb-3 flex items-center justify-between gap-4">
              <h2 className="font-semibold">Buyer decision</h2>
              <CloseButton onClose={closeAction} />
            </div>
            {renderAction(selectedAction) ?? <p role="status">This action is no longer in the buyer queue.</p>}
          </div>
        </ModalPanel>
      )}
      {snapshot && (
        <RiskDialog
          group={snapshot}
          current={current}
          live={groups.find((g) => g.key === snapshot.key)}
          decisions={decisions}
          issues={issues}
          setup={setup}
          onClose={close}
          onChanged={onChanged}
          renderDecision={renderDecision}
          renderAction={renderAction}
        />
      )}
    </section>
  );
}

const SEVERITY: Record<number, { label: string; dot: string }> = {
  5: { label: "Urgent", dot: "bg-signal-red" },
  4: { label: "High", dot: "bg-signal-red/70" },
  3: { label: "Today", dot: "bg-signal-amber" },
  2: { label: "Watch", dot: "bg-signal-green" },
  1: { label: "Info", dot: "bg-rule" },
};
const sev = (s: number) => SEVERITY[Math.min(5, Math.max(1, Math.round(s)))];

/** The one way severity is shown on the control room: a coloured dot and one word (Urgent, High, Today, Watch, Info). */
export function SeverityLabel({ severity, className }: { severity: number; className?: string }) {
  const s = sev(severity);
  return (
    <span className={cn("inline-flex items-center gap-1.5 whitespace-nowrap text-[12px] font-semibold text-muted", className)}>
      <span aria-hidden className={cn("size-2 shrink-0 rounded-full", s.dot)} />
      {s.label}
    </span>
  );
}

function RiskRow({ group, onOpen }: { group: RiskGroup; onOpen: () => void }) {
  const num = headline(group);
  const cta = group.actions.length > 1 ? "Compare actions" : group.actions.length === 1 ? "Review action" : "Review findings";
  const subject = [group.thing, group.company].filter(Boolean).join(" · ") || group.key;
  return (
    <article className="group relative grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-3 gap-y-1 px-3.5 py-3 hover:bg-dock/60 focus-within:bg-act-soft focus-within:ring-2 focus-within:ring-inset focus-within:ring-act sm:grid-cols-[minmax(0,1.7fr)_minmax(0,1fr)_auto] sm:px-5">
      <div className="col-start-1 row-start-1 min-w-0">
        <div className="flex flex-wrap items-baseline gap-x-2.5 gap-y-0.5">
          <h3 className="break-words text-[15px] font-semibold leading-snug text-ink">
            <button type="button" aria-haspopup="dialog" aria-label={`${cta} for ${group.title} · ${subject}`}
              onClick={e => { e.currentTarget.focus({ preventScroll: true }); onOpen(); }}
              className="text-left outline-none after:absolute after:inset-0 after:z-10 group-hover:underline">
              {group.title}
            </button>
          </h3>
          <SeverityLabel severity={group.severity} />
        </div>
        <p className="mt-0.5 break-words text-xs leading-snug text-muted">{subject}</p>
      </div>
      <p className="col-start-1 row-start-2 break-words text-sm leading-snug sm:col-start-2 sm:row-start-1">
        {num ? <><strong className={cn("tabular", num.stop && "text-stop")}>{num.value}</strong>{" "}{num.caption}</> :
          group.rating ? <><strong>{group.rating}</strong> risk rating</> : "Impact under review"}
      </p>
      <ChevronRight aria-hidden className="col-start-2 row-span-2 row-start-1 size-4 text-act sm:col-start-3 sm:row-span-1" />
    </article>
  );
}

/** JSON with sorted keys, so two action lists compare like Python's dict equality. */
const stableJson = (value: unknown): string =>
  JSON.stringify(value, (_k, v: unknown) =>
    v && typeof v === "object" && !Array.isArray(v)
      ? Object.fromEntries(Object.entries(v as Record<string, unknown>).sort(([a], [b]) => a.localeCompare(b)))
      : v,
  );

async function sha1Hex16(text: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-1", new TextEncoder().encode(text));
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("").slice(0, 16);
}

/**
 * Mirrors the backend's `already_requested` (backend/agents/planner_delivery.py): an option whose physical actions an
 * approved Planner card already chose, or a human review the Planner already opened, can't be approved again.
 * Returns the keys of such actions.
 */
function useAlreadyAuthorized(actions: RiskAction[], decisions: DecisionCard[], issues: Issue[]): Set<string> {
  const [blocked, setBlocked] = useState<Set<string>>(() => new Set());
  useEffect(() => {
    let alive = true;
    const chosen = new Set(
      decisions
        .filter((c) => c.created_by === "planner" && (c.status === "approved" || c.status === "executed") && c.chosen_option != null)
        .map((c) => stableJson(c.options[c.chosen_option!]?.actions ?? [])),
    );
    const openReviews = new Set(issues.filter((i) => i.raised_by === "planner" && i.status !== "resolved").map((i) => i.fingerprint));
    (async () => {
      const out = new Set<string>();
      for (const a of actions) {
        const steps = a.card && a.optionIndex != null ? a.card.options[a.optionIndex]?.actions ?? [] : [];
        if (!steps.length) continue;
        const first = steps[0] as Record<string, unknown>;
        if (first.type === "human_review") {
          const key = `${String(first.product_id)}/${String(first.part)}${first.reference ? `/${String(first.reference)}` : ""}`;
          if (crypto?.subtle && openReviews.has(await sha1Hex16(`recovery_review|${key}`))) out.add(a.key);
        } else if (chosen.has(stableJson(steps))) {
          out.add(a.key);
        }
      }
      if (alive) setBlocked(out);
    })().catch(() => undefined);
    return () => {
      alive = false;
    };
  }, [actions, decisions, issues]);
  return blocked;
}

function CloseButton({ onClose }: { onClose: () => void }) {
  return (
    <button type="button" onClick={onClose}
      className="flex shrink-0 items-center gap-1 rounded-md border border-rule bg-surface px-3 py-2 text-sm font-semibold text-muted hover:text-ink">
      <X aria-hidden className="size-4" /> Close
    </button>
  );
}

// ---------------------------------------------------------------------------
// Dialog: the numbers, the three best actions, everything else behind "More details"
// ---------------------------------------------------------------------------
function RiskDialog({
  group,
  current,
  live,
  decisions,
  issues,
  setup,
  onClose,
  onChanged,
  renderDecision,
  renderAction,
}: {
  /** The group as it was when opened: the three cards stay put while you approve one. */
  group: RiskGroup;
  current: boolean;
  /** The same group from the latest data (for "More details"); gone once nothing is open any more. */
  live: RiskGroup | undefined;
  /** Latest decisions and findings, to know what was decided or authorized since the dialog opened. */
  decisions: DecisionCard[];
  issues: Issue[];
  setup: CustomerSetup | null;
  onClose: () => void;
  onChanged: () => void;
  renderDecision?: (card: DecisionCard) => ReactNode;
  renderAction?: (key: string) => ReactNode;
}) {
  const [more, setMore] = useState(false);
  const [detail, setDetail] = useState<{ key: string; cardId?: number } | null>(null);
  const detailHeading = useRef<HTMLHeadingElement>(null);
  const returnTo = useRef<HTMLButtonElement>(null);
  const openDetail = (button: HTMLButtonElement, action: { key: string; cardId?: number }) => {
    returnTo.current = button;
    setDetail(action);
  };
  useLayoutEffect(() => {
    if (detail) {
      detailHeading.current?.focus();
    } else if (returnTo.current) {
      returnTo.current.focus();
      returnTo.current = null;
    }
  }, [detail]);
  const [approvedHere, setApprovedHere] = useState<Set<number>>(() => new Set());
  // The three cards stay put while the dialog is open, but a queue action follows its physical action to the new card
  // when the Planner superseded the old one (same queue key, new decision id and version), e.g. after a stale refusal.
  const top = useMemo(() => {
    const liveByKey = new Map((live?.actions ?? []).map((a) => [a.key, a]));
    return group.actions.slice(0, 3).map((a) => {
      const cur = liveByKey.get(a.key);
      return a.version && cur?.card && cur.version !== a.version ? cur : a;
    });
  }, [group, live]);
  const blocked = useAlreadyAuthorized(top, decisions, issues);
  /** A card stops being decidable once it is approved here, or anywhere (it leaves the open cards). */
  const decided = (card: DecisionCard | undefined) =>
    !!card && (approvedHere.has(card.id) || !decisions.some((d) => d.id === card.id && d.status === "proposed"));
  const matrix = setup?.approval_matrix ?? [];
  const lead = group.lead ?? group.findings[0];
  const evidence = group.findings.flatMap((f) => f.evidence ?? []);
  const num = headline(group);
  const short = group.futureSupply ? [] : group.editions.filter((e) => (e.shortfall ?? 0) > 0);

  const queued = live && renderAction ? live.actions.filter(a => a.key.startsWith("queue:")) : [];
  const shown = new Set(top.map(a => a.key));
  const covered = new Set([...queued, ...top].map(a => a.card?.id));
  const extra = [
    ...queued.filter(a => !shown.has(a.key)).map(a => ({ key: a.key, label: a.title, cardId: a.card?.id })),
    ...(live && renderDecision ? live.cards.filter(c => !covered.has(c.id)).map(c => ({ key: `card:${c.id}`, label: c.title, cardId: c.id })) : []),
  ];

  return (
    <ModalPanel label={detail ? "Buyer decision" : group.title} onClose={onClose}>
      {detail && (
        <div className="p-4 sm:p-6">
          <div className="sticky top-0 z-10 mb-3 flex items-center justify-between gap-3 border-b border-rule bg-dock py-2">
            <button type="button" className="flex items-center gap-1 text-sm font-semibold text-act hover:underline" onClick={() => setDetail(null)}>
              <ArrowLeft aria-hidden className="size-4" /> Back to risk
            </button>
            <CloseButton onClose={onClose} />
          </div>
          <h2 ref={detailHeading} tabIndex={-1} className="mb-2 text-sm font-semibold text-muted">Buyer decision · {group.title}</h2>
          {detail.key.startsWith("queue:") ? (renderAction?.(detail.key.slice(6)) ?? <p role="status">This action is no longer in the buyer queue.</p>) :
            (() => {
              const card = decisions.find(d => d.id === detail.cardId);
              return (card && renderDecision?.(card)) || <p role="status">This decision is no longer available.</p>;
            })()}
        </div>
      )}
      <div hidden={detail !== null} className="p-5 sm:p-8">
        <div className="flex items-start justify-between gap-4">
          <div className="min-w-0">
            <SeverityLabel severity={group.severity} className="text-[12.5px]" />
            <h2 id="risk-dialog-title" className="mt-2 text-[26px] font-bold leading-tight tracking-tight">
              {group.title}
            </h2>
            <p className="mt-1 text-[14px] text-muted">
              {[group.thing, group.company].filter(Boolean).join(" · ")}{" "}
              <span className="text-muted/70">· {group.key}</span>
            </p>
            {/* Executive summary: the reason in one line, the full reasoning stays under More details. */}
            {lead?.summary && <p className="mt-3 max-w-3xl text-[15px] leading-relaxed text-ink">{lead.summary}</p>}
          </div>
          <CloseButton onClose={onClose} />
        </div>

        {(num || short.length > 0) && (
          // Two equal boxes side by side on a phone; free width from sm up.
          <dl className="mt-5 grid grid-cols-2 gap-3 sm:flex sm:flex-wrap">
            {num && (
              <div className="min-w-0 rounded-2xl border border-rule bg-surface px-4 py-3 sm:min-w-[9rem]">
                <dt className="sr-only">{num.caption}</dt>
                <dd className={cn("text-[28px] font-bold leading-none tabular", num.stop ? "text-stop" : "text-ink")}>{num.value}</dd>
                <dd className="mt-1 text-[12.5px] text-muted">{num.caption}</dd>
              </div>
            )}
            {short
              .filter((e) => !(num && num.caption === `${e.edition} copies short`))
              .map((e) => (
                <div key={e.edition} className="min-w-0 rounded-2xl border border-rule bg-surface px-4 py-3 sm:min-w-[9rem]">
                  <dt className="sr-only">{e.edition} copies short</dt>
                  <dd className="text-[28px] font-bold leading-none text-ink tabular">{fmt(e.shortfall)}</dd>
                  <dd className="mt-1 text-[12.5px] text-muted">{e.edition} copies short</dd>
                </div>
              ))}
          </dl>
        )}

        <h3 className="mt-7 text-[15px] font-semibold">Best actions</h3>
        {top.length === 0 ? (
          <p className="mt-3 rounded-xl border border-dashed border-rule bg-surface px-4 py-6 text-sm text-muted">
            No action proposed yet.
          </p>
        ) : (
          <ul className="mt-3 grid gap-5 md:grid-cols-3">
            {top.map((a, i) => (
              <li
                // Keyed by the action, not its card: when it follows a superseded card the ActionCard keeps its state
                // ("Approved" survives the refresh) and only drops a leftover error (see ActionCard).
                key={a.key}
                style={{ perspective: "1200px", animationDelay: `${100 + i * 80}ms` }}
                className="animate-in fade-in slide-in-from-bottom-4 duration-500 fill-mode-both motion-reduce:animate-none"
              >
                <ActionCard
                  action={a}
                  rank={i}
                  current={current}
                  onFollowup={onClose}
                  setup={setup}
                  decided={decided(a.card)}
                  blocked={blocked.has(a.key)}
                  onApproved={(cardId) => {
                    setApprovedHere((ids) => new Set(ids).add(cardId));
                    onChanged();
                  }}
                  onStale={onChanged}
                />
                {((a.key.startsWith("queue:") && renderAction) || (a.card && renderDecision)) && (
                  <button type="button" className="mt-2 px-1 py-1 text-sm font-semibold text-act hover:underline"
                    aria-label={`Review evidence and approval for ${a.title}`}
                    onClick={e => openDetail(e.currentTarget, { key: a.key, cardId: a.card?.id })}>
                    Evidence &amp; approval <ArrowRight aria-hidden className="inline size-3.5" />
                  </button>
                )}
              </li>
            ))}
          </ul>
        )}

        <button
          type="button"
          aria-expanded={more}
          onClick={() => setMore((m) => !m)}
          className="mt-6 inline-flex items-center gap-1.5 text-[13.5px] font-semibold text-act hover:underline"
        >
          {more ? "Hide risk evidence" : "See risk evidence and related findings"}
          <ChevronDown aria-hidden className={cn("size-4 transition-transform", more && "rotate-180")} />
        </button>

        {more && (
          <div className="mt-3 space-y-4 animate-in fade-in duration-200">
            {lead && (
              <div className="rounded-2xl border border-rule bg-surface p-4 text-[14px] leading-relaxed">
                {lead.reason && <p>{lead.reason}</p>}
                {group.cards[0]?.reasoning && <p className="mt-3 whitespace-pre-line text-[13px] text-muted">{group.cards[0].reasoning}</p>}
                {evidence.length > 0 && <SourceList evidence={evidence} cards={group.cards} />}
                {group.findings.length > 0 && (
                  <details className="mt-2 text-sm">
                    <summary className="cursor-pointer font-semibold text-act">Related findings ({group.findings.length})</summary>
                    <ul className="mt-2 space-y-2">
                      {group.findings.map(f => <li key={f.id}>
                        <Link onClick={onClose} href={findingHref(f.id)} className="text-act underline">
                          {f.summary || `Finding #${f.id}`}
                        </Link>
                      </li>)}
                    </ul>
                  </details>
                )}
              </div>
            )}
            {lead?.raised_by && (
              <Link
                href={`/observability?agent=${encodeURIComponent(lead.raised_by)}`}
                className="inline-flex items-center gap-1.5 text-[13.5px] font-semibold text-act hover:underline"
              >
                See what the {AGENT_NAME[lead.raised_by] ?? lead.raised_by} did
                <ArrowRight aria-hidden className="size-3.5" />
              </Link>
            )}
            <ApprovalPolicy matrix={matrix} buyer={setup?.demo_buyer} rating={group.rating} />
            {extra.length > 0 && (
                <details className="text-sm">
                  <summary className="cursor-pointer font-semibold text-act">Other decisions ({extra.length})</summary>
                  <ul className="mt-2 space-y-2">{extra.map(a => <li key={a.key}>
                    <button type="button" className="text-left text-act underline" onClick={e => openDetail(e.currentTarget, a)}>{a.label}</button>
                  </li>)}</ul>
                </details>
            )}
          </div>
        )}
      </div>
    </ModalPanel>
  );
}

function ApprovalPolicy({ matrix, buyer, rating }: { matrix: Matrix; buyer?: string; rating: string | null }) {
  if (!matrix.length) return null;
  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-2 text-[12.5px] text-muted">
      <span className="font-semibold text-ink">Who approves</span>
      {matrix.map((r, i) => (
        <span key={i} className="tabular">
          {r.below_eur == null ? `≥ ${money(matrix[i - 1]?.below_eur ?? 0)}` : `< ${money(r.below_eur)}`}{" "}
          <span className="font-medium text-ink">{r.approvers.join(" + ")}</span>
        </span>
      ))}
      {(rating || buyer) && (
        <span className="ml-auto">
          {rating && `Risk grade ${rating}`}
          {rating && buyer && " · "}
          {buyer && `You: ${buyer}`}
        </span>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// 3D action card: title, two numbers, who approves, one button
// ---------------------------------------------------------------------------
const TYPE_ICON: Record<string, LucideIcon> = {
  expedite: Truck,
  email: Mail,
  "information request": Mail,
  second_source: Users,
  safety_stock: Boxes,
  block_stock: ShieldCheck,
  "human review": ClipboardCheck,
  human_review: ClipboardCheck,
  reallocate: Boxes,
  use_old_revision: PackageSearch,
};
const OWNER_STEP: Record<string, string> = {
  chaser: "Chaser asks the supplier",
  planner: "Planner prices this next",
  human: "Needs a person",
};
const RANK = ["Best", "2nd", "3rd"];

function ActionCard({
  action,
  current,
  onFollowup,
  rank,
  setup,
  decided,
  blocked,
  onApproved,
  onStale,
}: {
  action: RiskAction;
  current: boolean;
  onFollowup: () => void;
  rank: number;
  setup: CustomerSetup | null;
  /** Its decision card was approved since the dialog opened (here or elsewhere). */
  decided: boolean;
  /** The same physical action was already authorized on a related card. */
  blocked: boolean;
  onApproved: (cardId: number) => void;
  /** Approve was refused (e.g. the queue option changed): reload the data so the cards show the current options. */
  onStale: () => void;
}) {
  const [state, setState] = useState<"idle" | "busy" | "done" | "error">("idle");
  const [error, setError] = useState("");
  const [partial, setPartial] = useState<ActionRun | null>(null);
  // The action moved to a new card (the Planner superseded the old one after a stale refusal): clear the old error so
  // Approve shows again for the new card. An approval made here stays "Approved".
  const [cardId, setCardId] = useState(action.card?.id);
  if (action.card?.id !== cardId) {
    setCardId(action.card?.id);
    if (state === "error") {
      setState("idle");
      setError("");
    }
  }
  const Icon = TYPE_ICON[action.type] ?? Sparkles;
  const buyer = setup?.demo_buyer;
  const card = action.card;
  const option = card && action.optionIndex != null ? card.options[action.optionIndex] : undefined;
  const unnamed = option?.assignments?.filter((a) => !a.person).map((a) => a.required_role) ?? [];
  const routedHere =
    !!option && !!buyer && !unnamed.length && option.approvers?.length === 1 && option.approvers[0] === buyer &&
    option.available !== false;
  const step = option?.actions?.[0];
  const verb = option?.button_label || (step?.type === "human_review" ? "Open review"
    : step?.quote_inbox_id != null ? "Authorize quote" : step?.type === "email" ? "Send request" : "Approve");
  const canApprove = routedHere && (state === "done" || (!decided && !blocked));
  const approver = [action.roles.join(" + "), action.people.join(" + ")].filter(Boolean).join(" · ");
  const executable = !!option && option.available !== false;

  const approve = async () => {
    if (!current || !canApprove || !card || action.optionIndex == null || !buyer || state === "busy" || state === "done") return;
    setState("busy");
    setError("");
    try {
      const result = await postData<ActionRun>(`/api/decisions/${card.id}/approve`, {
        by: buyer,
        option: action.optionIndex,
        ...(action.version ? { version: action.version } : {}),
      });
      setPartial(result.status === "partial" ? result : null);
      setState("done");
      onApproved(card.id);
    } catch (e) {
      setError((e as Error).message);
      setState("error");
      if (action.version) onStale();
    }
  };

  return (
    <TiltCard
      maxTilt={8}
      className={cn(
        "h-[19rem] w-full rounded-2xl shadow-[0_24px_50px_-24px_rgba(38,50,40,0.55)] ring-1 ring-black/5",
        executable
          ? "bg-[linear-gradient(155deg,#2c3a30_0%,#4a5e4f_52%,#7b9082_100%)]"
          : "bg-[linear-gradient(155deg,#2a2d2b_0%,#3a3f3b_55%,#4d534e_100%)]",
      )}
    >
      <div aria-hidden className="pointer-events-none absolute inset-0 overflow-hidden rounded-2xl">
        <div className="absolute -left-16 -top-20 size-64 rounded-full bg-white/10 blur-3xl" />
        <Icon className="absolute -bottom-8 -right-8 size-44 text-white/[0.07]" strokeWidth={1.25} />
      </div>

      <div
        style={{ transform: "translateZ(42px)", transformStyle: "preserve-3d" }}
        className={cn("absolute inset-0 flex flex-col p-5 text-white transition-opacity duration-300", state === "done" && "opacity-0")}
      >
        <div className="flex items-start justify-between gap-3">
          <span className="flex items-center gap-2">
            <span
              className={cn(
                "rounded-full px-2.5 py-0.5 text-[11.5px] font-bold",
                rank === 0 ? "bg-white text-[#3e5143]" : "bg-white/15 text-white ring-1 ring-inset ring-white/25",
              )}
            >
              {RANK[rank] ?? `#${rank + 1}`}
            </span>
            <span title="Agent that proposed it" className="text-[11.5px] font-medium text-white/60">
              {action.source}
            </span>
          </span>
          <span
            style={{ transform: "translateZ(20px)" }}
            className="flex size-9 shrink-0 items-center justify-center rounded-full bg-white/15 ring-1 ring-inset ring-white/25"
          >
            <Icon aria-hidden className="size-[18px]" />
          </span>
        </div>

        <h4 style={{ transform: "translateZ(26px)" }} className="mt-3 line-clamp-3 text-[18px] font-semibold leading-snug tracking-tight">
          {action.title}
        </h4>

        <dl style={{ transform: "translateZ(16px)" }} className="mt-auto grid grid-cols-2 gap-2 pt-4">
          {(
            [
              ["Cost", action.costEur == null ? "Unknown" : money(action.costEur)],
              ["Potential gain", action.copiesGained == null ? "Unknown" : `${fmt(action.copiesGained)} copies`],
            ] as const
          ).map(([label, value]) => (
            <div key={label} className="rounded-lg bg-white/10 px-2.5 py-1.5 ring-1 ring-inset ring-white/15">
              <dt className="text-[10.5px] font-medium uppercase tracking-wide text-white/60">{label}</dt>
              <dd className="mt-0.5 truncate text-[16px] font-semibold tabular-nums">{value}</dd>
            </div>
          ))}
        </dl>

        <p className="mt-2.5 text-[12.5px] text-white/85" title={approver}>
          {approver ? `Approver: ${approver}` : action.approvalValue == null ? "Not priced yet" : ""}
        </p>

        <div style={{ transform: "translateZ(30px)" }} className="mt-2.5">
          {canApprove ? (
            <button
              type="button"
              onClick={approve}
              disabled={!current || state === "busy" || state === "done"}
              aria-label={`${verb}: ${action.title}`}
              className="flex w-full items-center justify-center gap-2 rounded-lg bg-white py-2.5 text-[14px] font-semibold text-[#3e5143] outline-none transition-[background-color,transform] hover:bg-white/90 focus-visible:ring-[3px] focus-visible:ring-white/60 active:scale-[0.98] disabled:opacity-70"
            >
              {state === "busy" && <Loader2 aria-hidden className="size-4 motion-safe:animate-spin" />}
              {state === "busy" ? "Submitting…" : !current ? "Waiting for current data" : verb}
            </button>
          ) : (
            <p className="w-full rounded-lg bg-white/10 py-2.5 text-center text-[13px] font-medium text-white/85 ring-1 ring-inset ring-white/20">
              {decided
                ? "Already decided"
                : blocked
                  ? "Already requested"
                  : executable
                    ? unnamed.length
                      ? `Waiting for a named ${unnamed.join(" + ").toLowerCase()}`
                      : `Awaiting ${action.people.join(" + ") || action.roles.join(" + ") || "approval"}`
                    : option
                      ? "Waiting for evidence"
                      : OWNER_STEP[action.owner] ?? "Suggested next step"}
            </p>
          )}
          {state === "error" && (
            <p role="alert" title={error} className="mt-1.5 line-clamp-2 text-[11.5px] leading-snug text-red-200">
              {error}
            </p>
          )}
        </div>
      </div>

      {state === "done" && (
        <div
          role="status"
          style={{ transform: "translateZ(90px)" }}
          className="absolute inset-0 flex flex-col items-center justify-center gap-3 rounded-2xl bg-[#2c3a30]/95 text-center text-white animate-in fade-in duration-300"
        >
          <span className="flex size-14 items-center justify-center rounded-full bg-white text-[#3e5143] animate-in zoom-in-50 duration-300">
            <Check aria-hidden className="size-7" strokeWidth={3} />
          </span>
          <p className="text-[16px] font-semibold">Approved</p>
          <p className="px-4 text-[13px] text-white/85">{partial ? `Follow-up needs attention${partial.failures.length ? " · " + [...new Set(partial.failures.map(f => AGENT_NAME[f.agent] || f.agent))].join(", ") : ""}` : buyer}</p>
          {card && <Link href={followupHref(card.id)} onClick={onFollowup}
            className="rounded px-2 py-1 text-sm font-semibold text-white underline">View decision follow-up</Link>}
        </div>
      )}
    </TiltCard>
  );
}
