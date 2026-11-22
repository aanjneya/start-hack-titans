"use client";

import Link from "next/link";
import { useCallback, useEffect, useId, useMemo, useRef, useState } from "react";
import {
  ago, API_URL, clock, eur, fmt, getData, parseTs, postData,
  type AgentLogEntry, type DecisionCard, type Forecast, type HealthCheckResponse, type InboxMessage,
  type Issue, type OutboxMessage, type RiskScore,
} from "@/lib/api";
import type { BuyerQueueEntry, CustomerSetup } from "../../API_CONTRACT";
import DataDrawer from "./data-drawer";
import { actionLine, entryDetail, humanizeAction } from "./observability/agent-meta";
import { RiskBoard, SeverityLabel } from "./risk-board";

type Snapshot = {
  /** The backend's ranked buyer queue. Null until it first loads: an older backend has none, and the tiles then
   *  rank the actions themselves. A failed refresh keeps the last queue. */
  queue: BuyerQueueEntry[] | null;
  setup: CustomerSetup | null;
  health: HealthCheckResponse | null;
  forecast: Forecast[];
  decisions: DecisionCard[];
  issues: Issue[];
  outbox: OutboxMessage[];
  inbox: InboxMessage[];
  risk: RiskScore[];
  trace: AgentLogEntry[];
};

const EMPTY: Snapshot = { queue: null, setup: null, health: null, forecast: [], decisions: [], issues: [], outbox: [], inbox: [], risk: [], trace: [] };

const AGENTS: { id: string; name: string; job: string; color: string }[] = [
  { id: "watcher", name: "Watcher", job: "Watches the supplier portal for new files", color: "#5a6478" },
  { id: "ingest", name: "Ingest", job: "Reads every file, scan and reply into one truth", color: "#1d5fb4" },
  { id: "reconciler", name: "Reconciler", job: "Checks the sources against each other", color: "#6b4fbb" },
  { id: "risk", name: "Risk rater", job: "Rates suppliers, parts and editions", color: "#a86a12" },
  { id: "planner", name: "Planner", job: "Forecasts packing and proposes decisions", color: "#127a7a" },
  { id: "chaser", name: "Chaser", job: "Asks suppliers for what is missing", color: "#2f7a4d" },
  { id: "human", name: "You", job: "Approve or reject", color: "#16213a" },
];
const agentColor = (id: string) => AGENTS.find((a) => a.id === id)?.color ?? "#5a6478";
const sentenceCase = (s: string) => (s ? s.charAt(0).toUpperCase() + s.slice(1) : s);
/** Trace names that are not one of the agents: supplier replies come in through the inbox; a replayed drop is the
 *  Watcher's work (as in the Agents panel), anything else the coordinator logs (its loop guard) stays "Coordinator". */
const OTHER_NAMES: Record<string, string> = { api: "Inbox" };
const agentName = (id: string, action?: string) =>
  AGENTS.find((a) => a.id === id)?.name ?? (id === "coordinator" && action === "drop" ? "Watcher" : OTHER_NAMES[id]) ?? sentenceCase(id);


/** "Email SUP-0028 · PO 4500318775/2 · SHP-2026-04313" for each action of an option (who it reaches). */
function actionTargets(actions: Record<string, unknown>[] | undefined): string[] {
  return (actions ?? []).map((a) => {
    const v = (k: string) => (a[k] == null || a[k] === "" ? "" : String(a[k]));
    const po = v("po_number") ? `PO ${v("po_number")}${v("po_line") ? `/${v("po_line")}` : ""}` : "";
    const type = v("type").replaceAll("_", " ");
    return [type && type[0].toUpperCase() + type.slice(1), v("supplier_id"), po, v("shipment_id"), v("part")]
      .filter(Boolean)
      .join(", ");
  });
}

async function loadSnapshot(): Promise<{ snap: Snapshot; online: boolean; missing: string[] }> {
  const [health, forecast, decisions, issues, outbox, inbox, risk, trace, setup, queue] = await Promise.allSettled([
    getData<HealthCheckResponse>("/api/health"),
    getData<Forecast[]>("/api/forecast"),
    getData<DecisionCard[]>("/api/decisions"),
    getData<Issue[]>("/api/issues"),
    getData<OutboxMessage[]>("/api/outbox"),
    getData<InboxMessage[]>("/api/inbox"),
    getData<RiskScore[]>("/api/risk"),
    getData<AgentLogEntry[]>("/api/trace?limit=500"),
    getData<CustomerSetup>("/api/setup"),
    getData<BuyerQueueEntry[]>("/api/buyer-queue"),
  ]);
  const val = <T,>(r: PromiseSettledResult<T>, fallback: T) => (r.status === "fulfilled" ? r.value : fallback);
  return {
    missing: Object.entries({forecast, decisions, issues, outbox, inbox, risk, trace, setup, queue})
      .filter(([, response]) => response.status === "rejected").map(([name]) => name),
    online: health.status === "fulfilled",
    snap: {
      setup: val(setup, null),
      queue: val(queue, null),
      health: val(health, null),
      forecast: val(forecast, []),
      decisions: val(decisions, []),
      issues: val(issues, []),
      outbox: val(outbox, []),
      inbox: val(inbox, []),
      risk: val(risk, []),
      trace: val(trace, []),
    },
  };
}

export default function ControlRoom() {
  const [snap, setSnap] = useState<Snapshot>(EMPTY);
  const [missing, setMissing] = useState<string[]>([]);
  const [online, setOnline] = useState<boolean | null>(null);
  const [now, setNow] = useState<number>(0);
  const [dropBusy, setDropBusy] = useState(false);
  /** When the running drop started: agents that log after this are working on it. */
  const [dropStartedAt, setDropStartedAt] = useState<number | null>(null);
  const [notice, setNotice] = useState<string>("");
  const [tab, setTab] = useState<"activity" | "findings" | "emails" | "risk">("activity");
  const [showData, setShowData] = useState(false);
  const closeData = useCallback(() => setShowData(false), []);

  const refresh = useCallback(async () => {
    const { snap: next, online: up, missing: failed } = await loadSnapshot();
    setOnline(up);
    setMissing(failed);
    if (up) setSnap(previous => ({...next, ...Object.fromEntries(
      failed.map(name => [name, previous[name as keyof Snapshot]]))}));
    setNow(Date.now());
  }, []);

  useEffect(() => {
    let alive = true;
    const tick = async () => {
      if (alive) await refresh();
    };
    void tick();
    const poll = setInterval(tick, 3000);
    const second = setInterval(() => setNow(Date.now()), 1000);
    return () => {
      alive = false;
      clearInterval(poll);
      clearInterval(second);
    };
  }, [refresh]);

  const nextDrop = async () => {
    setDropStartedAt(Date.now());
    setDropBusy(true);
    setNotice("");
    try {
      const r = await postData<Record<string, number | string | boolean>>("/api/replay/step");
      setNotice(
        r.done
          ? `All ${r.drops} recorded drops are in. Restart the backend to play them again.`
          : `Drop ${r.step} of ${r.of}: ${r.added} new files, ${r.changed} changed, ${r.removed} removed · ${r.open_issues} open findings`,
      );
    } catch (e) {
      setNotice(`The drop could not be processed: ${(e as Error).message}`);
    } finally {
      setDropBusy(false);
      await refresh();
    }
  };

  // Newest forecast per edition.
  const editions = useMemo(() => {
    const seen = new Map<string, Forecast>();
    for (const f of snap.forecast) if (!seen.has(f.edition)) seen.set(f.edition, f);
    return [...seen.values()].sort((a, b) => (b.shortfall ?? 0) - (a.shortfall ?? 0));
  }, [snap.forecast]);

  // Only collapse cards when every option and its approval route agree. Same headline but another PO stays a card of its own.
  const decisionGroups = useMemo(() => {
    const groups = new Map<string, DecisionCard[]>();
    for (const d of snap.decisions) {
      if (d.status !== "proposed") continue;
      const key = JSON.stringify([d.summary || d.title, d.recommended, d.options]);
      groups.set(key, [...(groups.get(key) ?? []), d]);
    }
    return [...groups.values()].map((cards) => ({ card: cards[0], similar: cards.slice(1) }));
  }, [snap.decisions]);

  const deadline = editions.find((f) => f.deadline)?.deadline ?? null;
  const mock = snap.health?.mockMode ?? true;

  return (
    <div className="theme-green min-h-screen bg-dock">
    <div className="mx-auto max-w-[1360px] px-4 pb-16 sm:px-6">
      <TopBar
        autonomy={snap.setup?.autonomy ?? null}
        matrix={snap.setup?.approval_matrix ?? []}
        buyer={snap.setup?.demo_buyer}
        online={online}
        mock={mock}
        deadline={deadline}
        now={now}
        busy={dropBusy}
        onDrop={nextDrop}
      />
      {notice && (
        <p role="status" className="mb-4 rounded-md border border-rule bg-surface px-4 py-2 text-sm">
          {notice}
        </p>
      )}
      {online === false && (
        <p role="alert" className="mb-4 rounded-md bg-stop-soft px-4 py-3 text-sm text-stop">
          No answer from the backend at {API_URL}. Start it from the repo root with{" "}
          <code className="font-semibold">backend/.venv/bin/uvicorn backend.app.main:app --port 8000</code>.
        </p>
      )}

      {missing.length > 0 && <p role="alert" className="mb-4 text-sm text-warn">
        Couldn&apos;t refresh {missing.join(", ")}. Showing the last data.
      </p>}
      {(!missing.includes("forecast") || editions.length > 0) && <Hero editions={editions} loading={online === null} />}

      <div className="mt-8 grid grid-cols-[minmax(0,1fr)] gap-8 lg:grid-cols-[minmax(0,1fr)_320px]">
        <RiskBoard
          decisions={snap.decisions}
          issues={snap.issues}
          risk={snap.risk}
          forecast={snap.forecast}
          setup={snap.setup}
          queue={snap.queue}
          emptyText={missing.includes("decisions") || online !== true ? "Waiting for current decisions." : "No decisions are waiting for approval."}
          onChanged={refresh}
          renderDecision={(card) => (
            <DecisionView
              setup={snap.setup}
              card={card}
              similar={decisionGroups.find((g) => g.card.id === card.id)?.similar ?? []}
              onDone={refresh}
            />
          )}
        />

        <aside aria-labelledby="agents" className="space-y-8">
          <AgentLanes
            trace={snap.trace}
            now={now}
            busy={dropBusy}
            since={dropStartedAt}
            onOpenData={() => setShowData(true)}
          />
          <CaseUpdates snap={snap} />
        </aside>
      </div>

      <section className="mt-10" aria-label="Details">
        <div role="tablist" className="mb-4 flex gap-1 overflow-x-auto border-b border-rule">
          {(
            [
              ["activity", "Activity"],
              ["findings", `Findings (${snap.issues.filter((i) => i.status !== "resolved").length})`],
              ["emails", `Emails (${snap.outbox.length})`],
              ["risk", "Risk"],
            ] as const
          ).map(([id, label]) => (
            <button
              key={id}
              role="tab"
              aria-selected={tab === id}
              onClick={() => setTab(id)}
              className={`-mb-px border-b-2 px-4 py-2 text-sm font-semibold ${
                tab === id ? "border-ink text-ink" : "border-transparent text-muted hover:text-ink"
              }`}
            >
              {label}
            </button>
          ))}
        </div>
        {tab === "activity" && <LiveLog trace={snap.trace} />}
        {tab === "findings" && <Findings issues={snap.issues} />}
        {tab === "emails" && <Emails outbox={snap.outbox} inbox={snap.inbox} mock={mock} onSent={refresh} />}
        {tab === "risk" && <Risk rows={snap.risk} />}
      </section>
      {showData && <DataDrawer onClose={closeData} />}
    </div>
    </div>
  );
}

type InboundEvidence = { eligible?: boolean; dock_released?: boolean; evidence?: {path: string}[] };

function forecastAfterReply(reply: InboxMessage, forecasts: Forecast[], edition: string) {
  if (!reply.result?.confirmed_delivery) return "";
  const history = forecasts.filter(f => f.edition === edition).sort((a, b) => a.id - b.id);
  for (const [index, f] of history.entries()) {
    const inbound = f.details.inbound;
    if (!Array.isArray(inbound)) continue;
    const arrival = (inbound as InboundEvidence[]).find(row => row.evidence?.some(e => e.path === `inbox/${reply.id}`));
    if (!arrival) continue;
    if (!arrival.dock_released) return "Waiting for the site to release a dock slot.";
    const previous = history[index - 1];
    if (f.projected_packed == null || f.shortfall == null || previous?.projected_packed == null) return "";
    const change = f.projected_packed - previous.projected_packed;
    return `${edition} forecast at confirmation: ${fmt(f.projected_packed)} by close (${change > 0 ? "+" : ""}${fmt(change)}), ${fmt(f.shortfall)} short.`;
  }
  return "";
}

function CaseUpdates({ snap }: { snap: Snapshot }) {
  const decided = snap.decisions.filter(d => d.status === "rejected" || (d.chosen_option != null && ["approved", "executed"].includes(d.status)))
    .sort((a, b) => (b.decided_at ?? "").localeCompare(a.decided_at ?? "") || b.id - a.id).slice(0, 4);
  if (!decided.length) return null;
  return (
    <section aria-labelledby="decided">
      <h2 id="decided" className="mb-3 text-xl font-bold">
        Decided
      </h2>
      <ul className="divide-y divide-rule rounded-lg border border-rule bg-surface">
        {decided.map((d) => {
          const option = d.options[d.chosen_option!];
          const sent = snap.outbox.find((o) => o.decision_id === d.id);
          const reply = sent && snap.inbox.filter((i) => i.outbox_id === sent.id).sort((a, b) => b.id - a.id)[0];
          const state = d.status === "rejected" ? "Rejected"
            : reply ? replyOutcome(reply)
            : sent?.status === "failed" ? "Email failed, check it"
            : sent?.status === "draft" ? "Email draft ready"
            : sent?.status === "sent" ? (snap.health?.mockMode && sent.sent_copy_pending == null ? "Simulated email sent" : "Email sent, waiting for reply")
            : sent?.status === "escalated" ? "Escalated to the buyer"
            : option?.actions.some((a) => a.type === "human_review") ? "With the buyer for review"
            : "Approved";
          const edition = snap.forecast.find((f) => d.title.startsWith(f.edition + " ·"))?.edition;
          const outcome = reply && edition ? forecastAfterReply(reply, snap.forecast, edition) : "";
          return (
            <li key={d.id} className="px-4 py-2.5">
              <p className="flex items-baseline justify-between gap-3">
                <span className="min-w-0 truncate text-sm font-semibold" title={option?.label ?? d.title}>
                  {option?.label ?? d.title}
                </span>
                <span className={`shrink-0 text-xs font-semibold ${d.status === "rejected" ? "text-stop" : "text-ok"}`}>
                  {d.status === "rejected" ? "✕" : "✓"} {d.decided_by}
                </span>
              </p>
              <p className="mt-0.5 text-xs text-muted">{state}</p>
              {outcome && <p className="mt-0.5 text-xs font-semibold">{outcome}</p>}
            </li>
          );
        })}
      </ul>
    </section>
  );
}

/** The human-in-the-loop switch as it is set in the customer setup, and who approves which amount (Soony's matrix). */
function ApprovalsChip({ autonomy, matrix, buyer }: {
  autonomy: CustomerSetup["autonomy"];
  matrix: CustomerSetup["approval_matrix"];
  buyer?: string;
}) {
  const [open, setOpen] = useState(false);
  // The header wraps on narrow screens, so the chip can sit on the left or the right: open the panel towards the room.
  const [alignRight, setAlignRight] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const panelId = useId();
  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent | KeyboardEvent) => {
      if (e instanceof KeyboardEvent ? e.key === "Escape" : !ref.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", close);
    return () => {
      document.removeEventListener("mousedown", close);
      document.removeEventListener("keydown", close);
    };
  }, [open]);
  const control = autonomy.mode === "control";
  const limit = approvalCurrency.format(autonomy.auto_execute_below_eur);
  return (
    <div
      ref={ref}
      className="relative"
      // Tabbing away closes it too (a click on the panel's own text has no related target and keeps it open).
      onBlur={(e) => {
        if (e.relatedTarget && !ref.current?.contains(e.relatedTarget)) setOpen(false);
      }}
    >
      <button
        type="button"
        aria-expanded={open}
        aria-controls={open ? panelId : undefined}
        onClick={() => {
          setAlignRight((ref.current?.getBoundingClientRect().right ?? 0) >= 304);
          setOpen((o) => !o);
        }}
        className="rounded bg-act-soft px-2 py-1 text-xs font-bold text-act outline-none hover:bg-act/15 focus-visible:ring-[3px] focus-visible:ring-ring/40"
      >
        {control ? "Approvals on" : `Auto < ${limit}`}
      </button>
      {open && (
        <div
          id={panelId}
          className={`absolute top-full z-30 mt-2 w-[min(18rem,calc(100vw-2rem))] rounded-xl border border-rule bg-surface p-4 text-sm shadow-lg animate-in fade-in slide-in-from-top-1 duration-150 ${alignRight ? "right-0" : "left-0"}`}>
          <p className="font-semibold text-ink">
            {control ? "Every action waits for its approver" : `Agents act on their own below ${limit}`}
          </p>
          {matrix.length > 0 && (
            <dl className="mt-3 space-y-1.5">
              {matrix.map((r, i) => (
                <div key={i} className="flex items-baseline justify-between gap-4">
                  <dt className="text-muted tabular">
                    {r.below_eur == null ? `≥ ${approvalCurrency.format(matrix[i - 1]?.below_eur ?? 0)}` : `< ${approvalCurrency.format(r.below_eur)}`}
                  </dt>
                  <dd className="text-right font-medium text-ink">{r.approvers.join(" + ")}</dd>
                </div>
              ))}
            </dl>
          )}
          {buyer && <p className="mt-3 border-t border-rule pt-2.5 text-xs text-muted">You approve as {buyer}</p>}
        </div>
      )}
    </div>
  );
}

function TopBar(props: {
  autonomy: CustomerSetup["autonomy"] | null;
  matrix: CustomerSetup["approval_matrix"];
  buyer?: string;
  online: boolean | null;
  mock: boolean;
  deadline: string | null;
  now: number;
  busy: boolean;
  onDrop: () => void;
}) {
  const end = parseTs(props.deadline);
  const left = end && props.now ? end.getTime() - props.now : null;
  const closes = end?.toLocaleString("en-GB", {
    weekday: "short", hour: "2-digit", minute: "2-digit", timeZone: "Europe/Berlin",
  });
  const status = props.online ? "Backend online" : props.online === false ? "Backend offline" : "Connecting";
  return (
    <header className="flex flex-wrap items-center justify-between gap-4 py-5">
      {/* The one number that matters up here: time left until packing closes. */}
      <h1 className="flex items-baseline gap-2" title={closes ? `Packing closes ${closes}` : undefined}>
        {left == null ? (
          <span className="text-lg font-semibold text-muted">Packing deadline</span>
        ) : left <= 0 ? (
          <span className="text-2xl font-bold">Packing has closed</span>
        ) : (
          <>
            <span className="text-[30px] font-bold leading-none tabular">
              {Math.floor(left / 3_600_000)} h {String(Math.floor((left % 3_600_000) / 60_000)).padStart(2, "0")} min
            </span>
            <span className="text-sm text-muted">until packing closes</span>
          </>
        )}
      </h1>
      <div className="flex flex-wrap items-center gap-2 sm:gap-3">
        {props.autonomy && <ApprovalsChip autonomy={props.autonomy} matrix={props.matrix} buyer={props.buyer} />}
        <span
          title={props.mock ? "MOCK_MODE=true: no AI calls, no real emails" : "Live: real AI calls and emails"}
          className={`rounded px-2 py-1 text-xs font-bold ${props.mock ? "bg-warn-soft text-warn" : "bg-ok-soft text-ok"}`}
        >
          {props.mock ? "Mock" : "Live"}
        </span>
        <span
          role="img"
          aria-label={status}
          title={status}
          className={`inline-block h-2.5 w-2.5 rounded-full ${
            props.online ? "bg-ok" : props.online === false ? "bg-stop" : "bg-rule"
          }`}
        />
        <button
          onClick={props.onDrop}
          disabled={props.busy || !props.online}
          className="rounded-md bg-ink px-4 py-2 text-sm font-semibold text-white hover:bg-act disabled:cursor-not-allowed disabled:opacity-50"
          title="Sends the next real portal drop (from git history) through all agents. The first click starts from an empty store."
        >
          {props.busy ? (
            "Agents are working"
          ) : (
            <>
              <span className="sm:hidden">Next drop</span>
              <span className="hidden sm:inline">Play next data drop</span>
            </>
          )}
        </button>
        <Link
          href="/observability"
          className="rounded-md border border-rule bg-surface px-3.5 py-2 text-sm font-semibold text-ink hover:border-act hover:text-act"
          title="Open dedicated AI Observability dashboard with per-agent visual reports"
        >
          <span className="sm:hidden">Agents</span>
          <span className="hidden sm:inline">AI Observability</span>
        </Link>
      </div>
    </header>
  );
}

function Hero({ editions, loading }: { editions: Forecast[]; loading: boolean }) {
  // First paint, before the backend has answered: quiet placeholders, not "no forecast yet".
  if (editions.length === 0 && loading) {
    return (
      <section aria-label="Forecast" aria-busy="true" className="grid gap-px overflow-hidden rounded-xl bg-rule sm:grid-cols-2">
        {[0, 1].map((i) => (
          <div key={i} className="bg-surface px-6 py-6">
            <div className="h-[clamp(56px,8vw,104px)] w-3/5 animate-pulse rounded-lg bg-dock" />
            <div className="mt-4 h-5 w-2/5 animate-pulse rounded bg-dock" />
          </div>
        ))}
      </section>
    );
  }
  if (editions.length === 0) {
    return (
      <section className="rounded-xl bg-surface px-6 py-10 text-muted">
        No forecast yet. Press <strong className="text-ink">Play next data drop</strong> to load the first drop.
      </section>
    );
  }
  return (
    <section aria-label="Forecast" className="grid gap-px overflow-hidden rounded-xl bg-rule sm:grid-cols-2">
      {editions.map((f) => {
        const short = (f.shortfall ?? 0) > 0;
        return (
          <div key={f.edition} className="bg-surface px-6 py-6">
            <p className={`figure text-[clamp(64px,10vw,124px)] ${short ? "text-stop" : "text-ok"}`}>
              {f.shortfall == null ? "—" : fmt(f.shortfall)}
            </p>
            <p className="mt-2 text-lg font-semibold">
              {f.shortfall == null ? `${f.edition}: no forecast yet` : short ? `${f.edition} copies short` : `${f.edition} on track`}
            </p>
          </div>
        );
      })}
    </section>
  );
}

const approvalCurrency = new Intl.NumberFormat("en-IE", {
  style: "currency", currency: "EUR", minimumFractionDigits: 0, maximumFractionDigits: 2,
});

function DecisionView({ setup, card, similar, onDone }: { setup: CustomerSetup | null; card: DecisionCard; similar: DecisionCard[]; onDone: () => void }) {
  const [choice, setChoice] = useState<number>(card.recommended ?? 0);
  const [why, setWhy] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const option = card.options[choice];
  const buyer = setup?.demo_buyer;
  const assigned = option?.approvers ?? [];
  const recommended = card.options[card.recommended ?? 0];
  const ownsCard = !!buyer && recommended?.approvers?.length === 1 && recommended.approvers[0] === buyer;
  const canDecide = ownsCard && assigned.length === 1 && assigned[0] === buyer && option?.available !== false;
  const value = option?.approval_value_eur;
  const matrix = setup?.approval_matrix ?? [];
  const band = value == null ? -1 : matrix.findIndex(rule => rule.below_eur == null || value < rule.below_eur);
  const rule = matrix[band];
  const lower = matrix[band - 1]?.below_eur ?? 0;
  const roles = option?.approval_roles ?? rule?.approvers ?? [];
  const roleLabel = roles.join(" + ");
  const awaiting = roleLabel || assigned.join(" + ") || "approval routing";
  const people = assigned.filter(name => !roles.includes(name));
  const quoted = option?.actions.some(action => action.quote_inbox_id != null);

  const decide = async (verb: "approve" | "reject") => {
    if (!canDecide || !buyer) return;
    setBusy(true);
    setError("");
    try {
      await postData(`/api/decisions/${card.id}/${verb}`, {
        by: buyer,
        option: verb === "approve" ? choice : undefined,
      });
      onDone();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <article className="rounded-lg border border-rule bg-surface">
      <div className="px-5 pt-4">
        <h3 className="text-lg font-bold leading-snug">{card.summary || card.title}</h3>
        <p className="mt-1 text-sm text-muted">
          {card.title}
          {similar.length > 0 && ` and ${similar.length} similar ${similar.length === 1 ? "card" : "cards"}`}
        </p>
      </div>

      <fieldset className="mt-3 border-t border-rule">
        <legend className="sr-only">Options</legend>
        {card.options.map((o, i) => {
          const off = o.available === false || !ownsCard;
          return (
            <label
              key={i}
              className={`flex cursor-pointer gap-3 border-b border-rule px-5 py-3 last:border-b-0 ${
                choice === i ? "bg-act-soft" : "hover:bg-dock"
              } ${off ? "cursor-not-allowed opacity-50" : ""}`}
            >
              <input
                type="radio"
                name={`card-${card.id}`}
                className="mt-1 accent-[var(--color-act)]"
                checked={choice === i}
                disabled={off}
                onChange={() => setChoice(i)}
              />
              <span className="min-w-0 flex-1">
                <span className="flex flex-wrap items-baseline gap-x-3">
                  <span className="font-semibold">{o.label}</span>
                  {card.recommended === i && <span className="text-xs font-semibold text-act">Recommended</span>}
                </span>
                {o.description && <span className="block text-sm text-muted">{o.description}</span>}
                {actionTargets(o.actions).map((t) => (
                  <span key={t} className="block text-sm text-ink">
                    {t}
                  </span>
                ))}
                <span className="mt-1 flex flex-wrap gap-x-4 text-sm tabular">
                  <span>Costs {eur(o.cost_eur)}</span>
                  <span>Gains {fmt(o.copies_gained)} copies</span>
                  {o.remaining_shortfall != null && <span>Still short {fmt(o.remaining_shortfall)}</span>}
                </span>
              </span>
            </label>
          );
        })}
      </fieldset>

      <div className="flex flex-wrap items-center gap-3 border-t border-rule px-5 py-3">
        <dl className="flex w-full flex-wrap gap-x-8 gap-y-2 text-sm">
          {quoted && <div><dt className="text-muted">Extra charge</dt><dd>{option.cost_eur == null ? "Unknown" : approvalCurrency.format(option.cost_eur)}</dd></div>}
          <div><dt className="text-muted">Approval value</dt><dd>{value == null ? "Unknown" : approvalCurrency.format(value)}</dd></div>
          <div><dt className="text-muted">Required role</dt><dd>{roleLabel || "Pending"}</dd></div>
          <div><dt className="text-muted">Assigned to</dt><dd>{people.join(" + ") || "Pending assignment"}</dd></div>
        </dl>
        {canDecide ? (
          <>
            <button onClick={() => decide("approve")} disabled={busy}
              className="rounded-md bg-act px-4 py-2 text-sm font-semibold text-white hover:bg-ink disabled:opacity-50">
              Approve
            </button>
            <button onClick={() => decide("reject")} disabled={busy}
              className="rounded-md border border-rule px-4 py-2 text-sm font-semibold hover:bg-dock disabled:opacity-50">
              Reject
            </button>
          </>
        ) : <span className="text-sm font-semibold">Awaiting {awaiting}</span>}
        <button
          onClick={() => setWhy((w) => !w)}
          aria-expanded={why}
          className="ml-auto text-sm font-semibold text-act hover:underline"
        >
          {why ? "Hide reasoning" : "Why?"}
        </button>
      </div>
      {error && <p className="px-5 pb-3 text-sm text-stop">{error}</p>}
      {why && (
        <div className="border-t border-rule bg-dock/60 px-5 py-4 text-sm">
          {rule && value != null && (
            <p>Approval policy routes {approvalCurrency.format(value)} to {rule.approvers.join(" + ")}
              {" "}for amounts from {approvalCurrency.format(lower)}
              {rule.below_eur == null ? " upwards" : ` to below ${approvalCurrency.format(rule.below_eur)}`}.
              {quoted &&
                " The approval value includes the original PO and the extra quoted charge."}
            </p>
          )}
          {card.problem && <p className="font-semibold">{card.problem}</p>}
          {card.reasoning && <p className="mt-2 whitespace-pre-line">{card.reasoning}</p>}
          {option?.risk_note && <p className="mt-2 text-muted">Risk of this option: {option.risk_note}</p>}
          {card.evidence.length > 0 && (
            <>
              <p className="mt-3 font-semibold">Evidence</p>
              <ul className="mt-1 space-y-1">
                {card.evidence.slice(0, 8).map((e, i) => (
                  <li key={i} className="break-words text-muted">
                    <span className="text-ink">{e.path}</span>
                    {e.locator && `, ${e.locator}`}
                    {e.quote && `: “${e.quote}”`}
                  </li>
                ))}
              </ul>
            </>
          )}
        </div>
      )}
    </article>
  );
}

function AgentLanes({
  trace,
  now,
  busy,
  since,
  onOpenData,
}: {
  trace: AgentLogEntry[];
  now: number;
  /** A data drop is running right now. */
  busy: boolean;
  /** When it started (ms), to tell which agents already picked it up. */
  since: number | null;
  onOpenData: () => void;
}) {
  const stats = useMemo(() => {
    const out = new Map<string, { last?: AgentLogEntry; count: number }>();
    for (const e of trace) {
      // In replay mode the coordinator hands the drop over; detecting drops is the Watcher's job, so show it there.
      const agent = e.agent === "coordinator" && e.action === "drop" ? "watcher" : e.agent;
      const s = out.get(agent) ?? { count: 0 };
      // Show the newest real step, not the internal "handle:<event>" hand-off lines.
      if (!s.last && !e.action.startsWith("handle:") && e.action !== "unhandled") s.last = e;
      s.count += 1;
      out.set(agent, s);
    }
    return out;
  }, [trace]);
  return (
    <section aria-labelledby="agents">
      <div className="mb-3 flex items-baseline justify-between gap-3">
        <h2 id="agents" className="flex items-baseline gap-2 text-xl font-bold">
          Agents
          {busy && <span className="text-sm font-semibold text-act">working on the drop</span>}
        </h2>
        <button
          onClick={onOpenData}
          className="text-sm font-semibold text-act hover:underline"
          title="The data the Watcher fetched and Ingest cleaned, with SAP table and field names"
        >
          SAP data
        </button>
      </div>
      <div className="relative overflow-hidden rounded-lg border border-rule bg-surface">
        {busy && (
          <div aria-hidden className="absolute inset-x-0 top-0 z-10 h-0.5 overflow-hidden bg-act-soft">
            <span className="agent-glint" />
          </div>
        )}
      <ul className="divide-y divide-rule">
        {AGENTS.map((a) => {
          const s = stats.get(a.id);
          const ts = parseTs(s?.last?.ts);
          // During a drop: an agent that logged after it started is on it (they pick it up in parallel), the rest wait.
          const onDrop = busy && !!ts && since != null && ts.getTime() >= since - 1000;
          const active = onDrop || (ts && now ? now - ts.getTime() < 15_000 : false);
          const waiting = busy && !onDrop && a.id !== "human";
          return (
            <li key={a.id}>
              <Link
                href={`/observability?agent=${a.id}`}
                title={a.job}
                className="flex items-center gap-2.5 px-4 py-2.5 transition-colors hover:bg-dock/60"
              >
                <span
                  aria-hidden
                  className={`inline-block h-2 w-2 shrink-0 rounded-full transition-opacity ${active ? "beat" : ""}`}
                  style={{ background: a.color, opacity: waiting ? 0.35 : s ? 1 : 0.3 }}
                />
                <span className="min-w-0 flex-1">
                  <span className="block text-sm font-semibold">{a.name}</span>
                  <span className="block truncate text-xs text-muted" title={s?.last ? entryDetail(s.last) : undefined}>
                    {/* An error shows only while it is the agent's newest step; one old hiccup does not pin the lane. */}
                    {s?.last?.action === "error" ? (
                      <span className="font-semibold text-stop">Error</span>
                    ) : waiting ? (
                      "Waiting"
                    ) : s?.last ? (
                      sentenceCase(actionLine(s.last))
                    ) : (
                      "Idle"
                    )}
                  </span>
                </span>
                <span className="shrink-0 text-xs text-muted tabular">{s ? ago(s.last?.ts, now) : ""}</span>
              </Link>
            </li>
          );
        })}
      </ul>
      </div>
    </section>
  );
}

type LogRow = { key: string; e: AgentLogEntry; count: number; details: string[]; oldest: number };

/** One agent doing the same thing within one run becomes one row with a count: a drop re-checks every finding, and the
 *  Planner words each card with an AI call between the cards. Entries without a run id fold only with their neighbour.
 *  The trace is newest first, so a row shows its newest entry; its key (run + agent + action, or the oldest entry id)
 *  stays the same while newer entries of that run arrive, so an opened "+N more" list stays open. */
function foldLog(trace: AgentLogEntry[], max = 40): LogRow[] {
  const rows: LogRow[] = [];
  const byRun = new Map<string, LogRow>();
  let last: LogRow | undefined; // the row the previous entry went into
  for (const e of trace) {
    if (e.action.startsWith("handle:") || e.action === "unhandled") continue;
    const runKey = e.run_id ? `${e.run_id}|${e.agent}|${e.action}` : null;
    let row = runKey
      ? byRun.get(runKey)
      : last && !last.e.run_id && last.e.agent === e.agent && last.e.action === e.action
        ? last
        : undefined;
    // Past the row limit, older entries still count towards the rows already shown.
    if (!row && rows.length < max) {
      row = { key: runKey ?? "", e, count: 0, details: [], oldest: e.id };
      rows.push(row);
      if (runKey) byRun.set(runKey, row);
    }
    if (row) {
      row.count += 1;
      row.oldest = e.id;
      const detail = entryDetail(e);
      if (detail && !row.details.includes(detail)) row.details.push(detail);
    }
    last = row;
  }
  for (const r of rows) if (!r.key) r.key = `id:${r.oldest}`;
  return rows;
}

function LiveLog({ trace }: { trace: AgentLogEntry[] }) {
  const rows = useMemo(() => foldLog(trace), [trace]);
  return (
    <section aria-label="What the agents just did">
      <ol className="max-h-[420px] overflow-y-auto rounded-lg border border-rule bg-surface text-sm">
        {rows.length === 0 && <li className="px-4 py-6 text-muted">The agents have not done anything yet.</li>}
        {rows.map(({ key, e, count, details }) => (
          <li key={key} className="flex gap-3 border-b border-rule px-4 py-2.5 last:border-b-0">
            <span className="w-16 shrink-0 pt-0.5 text-xs text-muted tabular">{clock(e.ts)}</span>
            <span className="min-w-0 flex-1">
              <span className="flex items-center gap-2">
                <span aria-hidden className="h-2 w-2 shrink-0 rounded-full" style={{ background: agentColor(e.agent) }} />
                <span className="font-semibold">{agentName(e.agent, e.action)}</span>
                <span className={e.action === "error" ? "font-semibold text-stop" : "text-muted"}>
                  {sentenceCase(humanizeAction(e.action).label)}
                </span>
                {count > 1 && <span className="text-xs font-semibold text-muted tabular">×{count}</span>}
              </span>
              {details.length > 1 ? (
                <details className="group mt-0.5 text-muted">
                  <summary className="cursor-pointer list-none break-words [&::-webkit-details-marker]:hidden">
                    {details[0]}{" "}
                    <span className="whitespace-nowrap font-semibold text-act group-open:hidden">+{details.length - 1} more</span>
                  </summary>
                  <ul className="mt-1 space-y-1">
                    {details.slice(1).map((d) => (
                      <li key={d} className="break-words">{d}</li>
                    ))}
                  </ul>
                </details>
              ) : (
                details[0] && <span className="mt-0.5 block break-words text-muted">{details[0]}</span>
              )}
            </span>
          </li>
        ))}
      </ol>
    </section>
  );
}

function Findings({ issues }: { issues: Issue[] }) {
  const rows = [...issues].sort(
    (a, b) => Number(a.status === "resolved") - Number(b.status === "resolved") || b.severity - a.severity,
  );
  if (rows.length === 0) return <p className="text-muted">No findings yet.</p>;
  return (
    <div className="overflow-x-auto rounded-lg border border-rule bg-surface">
      <table className="w-full text-left text-sm">
        <thead className="border-b border-rule text-muted">
          <tr>
            <th className="px-4 py-2 font-semibold">Severity</th>
            <th className="px-4 py-2 font-semibold">Finding</th>
            <th className="px-4 py-2 font-semibold">Agent</th>
            <th className="px-4 py-2 font-semibold">Status</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((i) => {
            return (
              <tr key={i.id} className={`border-b border-rule last:border-b-0 ${i.status === "resolved" ? "opacity-50" : ""}`}>
                <td className="px-4 py-2 align-top">
                  <SeverityLabel severity={i.severity} className="pt-0.5" />
                </td>
                <td className="px-4 py-2 align-top">
                  {i.summary}
                  {i.evidence[0]?.path && <span className="block text-xs text-muted">{i.evidence[0].path}</span>}
                </td>
                <td className="px-4 py-2 align-top">{agentName(i.raised_by)}</td>
                <td className="px-4 py-2 align-top">{sentenceCase(i.status)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function Emails({ outbox, inbox, mock, onSent }: { outbox: OutboxMessage[]; inbox: InboxMessage[]; mock: boolean; onSent: () => void }) {
  if (outbox.length === 0) return <p className="text-muted">No supplier emails yet. The Chaser writes one when a finding needs an answer.</p>;
  return (
    <ul className="space-y-3">
      {outbox.map((m) => (
        <li key={m.id}>
          <EmailView m={m} replies={inbox.filter((r) => r.outbox_id === m.id)} mock={mock} onSent={onSent} />
        </li>
      ))}
    </ul>
  );
}

function EmailView({ m, replies, mock, onSent }: { m: OutboxMessage; replies: InboxMessage[]; mock: boolean; onSent: () => void }) {
  const [open, setOpen] = useState(false);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const status = m.status === "sent" && mock && m.sent_copy_pending == null ? "simulated" : m.status;

  const reply = async () => {
    setBusy(true);
    setError("");
    try {
      await postData("/api/inbox", {
        supplier_id: m.supplier_id, body: text, issue_id: m.issue_id ?? undefined, outbox_id: m.id,
        subject: `Re: ${m.subject}`,
      });
      setText("");
      onSent();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <article className="rounded-lg border border-rule bg-surface">
      <button onClick={() => setOpen((o) => !o)} aria-expanded={open} className="flex w-full flex-wrap items-baseline gap-x-3 px-5 py-3 text-left">
        <span className="font-semibold">{m.subject}</span>
        <span className="text-sm text-muted">
          to {m.to_email} in {m.language.toUpperCase()}
        </span>
        <span
          className={`ml-auto rounded px-2 py-0.5 text-xs font-semibold capitalize ${
            status === "replied" ? "bg-ok-soft text-ok" : status === "simulated" ? "bg-warn-soft text-warn" : "bg-act-soft text-act"
          }`}
        >
          {status}
        </span>
      </button>
      {open && (
        <div className="border-t border-rule px-5 py-4 text-sm">
          <p className="whitespace-pre-line">{m.body_en || m.body}</p>
          {replies.map((r) => (
            <div key={r.id} className="mt-4 border-l-2 border-ok pl-3">
              <p className="text-xs text-muted">Reply from {r.supplier_id}, {clock(r.received_at)}</p>
              <p className="whitespace-pre-line">{r.body}</p>
              <p className="mt-1 text-xs text-muted">{replyOutcome(r)}</p>
            </div>
          ))}
          <label className="mt-4 block text-xs font-semibold text-muted" htmlFor={`reply-${m.id}`}>
            Test: answer as the supplier
          </label>
          <textarea
            id={`reply-${m.id}`}
            value={text}
            onChange={(e) => setText(e.target.value)}
            rows={2}
            className="mt-1 w-full rounded-md border border-rule p-2"
            placeholder="We confirm revision C, delivery to DC-HAM on 22.11.2026."
          />
          <button
            onClick={reply}
            disabled={busy || !text.trim()}
            className="mt-2 rounded-md bg-ink px-3 py-1.5 text-sm font-semibold text-white hover:bg-act disabled:opacity-50"
          >
            Send test reply
          </button>
          {error && <p className="mt-2 text-stop">{error}</p>}
        </div>
      )}
    </article>
  );
}

/** What Ingest did with a supplier reply, in one sentence. */
function replyOutcome(r: InboxMessage): string {
  const res = r.result ?? {};
  if (!r.processed) return "Waiting for Ingest.";
  if (typeof res.review === "string") return `Held for review: ${res.review}`;
  const advice = (res.confirmed_delivery ?? res.supplier_quote) as Record<string, unknown> | undefined;
  if (advice) {
    const when = parseTs(String(advice.arrival))?.toLocaleString("en-GB", {
      weekday: "short", day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", timeZone: "Europe/Berlin",
    });
    return res.supplier_quote
      ? `Ingest recorded a quote for ${fmt(Number(advice.quantity))} pcs. The Planner can now compare the offer.`
      : `Ingest confirmed ${fmt(Number(advice.quantity))} pcs, revision ${String(advice.revision)}, due ${when} Berlin time. The Planner checks dock release and packing time.`;
  }
  const fields = Array.isArray(res.fields) ? (res.fields as string[]) : [];
  return fields.length
    ? `Ingest filled ${fields.join(" and ")} on ${String(res.rows)} order line(s).`
    : "Read by Ingest; nothing to fill.";
}

function Risk({ rows }: { rows: RiskScore[] }) {
  if (rows.length === 0) return <p className="text-muted">No risk ratings yet.</p>;
  // Every re-rating adds rows; keep the newest per supplier, part or edition.
  const newest = new Map<string, RiskScore>();
  for (const r of rows) {
    const key = `${r.entity_type}/${r.entity_id}`;
    if ((newest.get(key)?.id ?? -1) < r.id) newest.set(key, r);
  }
  const sorted = [...newest.values()].sort((a, b) => b.grade - a.grade || a.entity_id.localeCompare(b.entity_id));
  return (
    <div className="overflow-x-auto rounded-lg border border-rule bg-surface">
      <table className="w-full text-left text-sm">
        <thead className="border-b border-rule text-muted">
          <tr>
            <th className="px-4 py-2 font-semibold">Rating</th>
            <th className="px-4 py-2 font-semibold">Supplier, part or edition</th>
            <th className="px-4 py-2 font-semibold">Main reason</th>
          </tr>
        </thead>
        <tbody>
          {sorted.map((r) => (
            <tr key={r.id} className="border-b border-rule last:border-b-0">
              <td className={`px-4 py-2 align-top font-bold tabular ${r.grade >= 5 ? "text-stop" : r.grade >= 4 ? "text-warn" : "text-ok"}`}>
                {r.rating} <span className="font-normal text-muted">({r.grade}/6)</span>
              </td>
              <td className="px-4 py-2 align-top">
                <span className="font-semibold">{r.entity_id}</span>
                <span className="block text-xs capitalize text-muted">{r.entity_type}</span>
              </td>
              <td className="px-4 py-2 align-top">{r.reasons[0]?.text ?? ""}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
