"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ago, API_URL, ApiHttpError, clock, fmt, getData, parseTs, postData,
  type AgentLogEntry, type DecisionCard, type Forecast, type HealthCheckResponse, type InboxMessage,
  type Issue, type OutboxMessage, type RiskScore,
} from "@/lib/api";
import type { BuyerQueueEntry, CustomerSetup, RunResult } from "../../API_CONTRACT";
import { ExposureEvidence, ExposureSummary } from "./supply-exposure";
import DataDrawer from "./data-drawer";

type Snapshot = {
  queue: BuyerQueueEntry[];
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

const EMPTY: Snapshot = { queue: [], setup: null, health: null, forecast: [], decisions: [], issues: [], outbox: [], inbox: [], risk: [], trace: [] };

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

const SEVERITY: Record<number, { label: string; cls: string }> = {
  5: { label: "Critical", cls: "bg-stop text-white" },
  4: { label: "Copies at risk", cls: "bg-stop-soft text-stop" },
  3: { label: "Answer today", cls: "bg-warn-soft text-warn" },
  2: { label: "Watch", cls: "bg-act-soft text-act" },
  1: { label: "Info", cls: "bg-dock text-muted" },
};

async function loadSnapshot(health: HealthCheckResponse): Promise<{ snap: Snapshot; missing: string[] }> {
  const [forecast, decisions, issues, outbox, inbox, risk, trace, setup, queue] = await Promise.allSettled([
    getData<Forecast[]>("/api/forecast"),
    getData<DecisionCard[]>("/api/decisions"),
    getData<Issue[]>("/api/issues"),
    getData<OutboxMessage[]>("/api/outbox"),
    getData<InboxMessage[]>("/api/inbox"),
    getData<RiskScore[]>("/api/risk"),
    getData<AgentLogEntry[]>("/api/trace?limit=400"),
    getData<CustomerSetup>("/api/setup"),
    getData<BuyerQueueEntry[]>("/api/buyer-queue"),
  ]);
  const val = <T,>(r: PromiseSettledResult<T>, fallback: T) => (r.status === "fulfilled" ? r.value : fallback);
  return {
    missing: Object.entries({forecast, decisions, issues, outbox, inbox, risk, trace, setup, queue})
      .filter(([, response]) => response.status === "rejected").map(([name]) => name),
    snap: {
      setup: val(setup, null),
      queue: val(queue, []),
      health,
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
  const [notice, setNotice] = useState<string>("");
  const [tab, setTab] = useState<"findings" | "emails" | "risk">("findings");
  const [activeMail, setActiveMail] = useState<number | null>(null);
  const [showData, setShowData] = useState(false);
  const closeData = useCallback(() => setShowData(false), []);

  const lastSnapshot = useRef({version: "", fetched: 0});
  const request = useRef(0);
  const refresh = useCallback(async (force = true) => {
    const id = ++request.current;
    let health: HealthCheckResponse;
    try {
      health = await getData<HealthCheckResponse>("/api/health");
    } catch {
      if (id === request.current) {
        setOnline(false);
        lastSnapshot.current.fetched = 0;
      }
      return;
    }
    if (id !== request.current) return;
    setOnline(true);
    // Force a full read after user actions and periodically for unlogged external changes.
    if (!force && health.activity_version && health.activity_version === lastSnapshot.current.version
      && Date.now() - lastSnapshot.current.fetched < 30_000) return;
    const { snap: next, missing: failed } = await loadSnapshot(health);
    if (id !== request.current) return;
    setMissing(failed);
    setSnap(previous => ({...next, ...Object.fromEntries(
      failed.map(name => [name, previous[name as keyof Snapshot]]))}));
    lastSnapshot.current = {version: health.activity_version ?? "", fetched: failed.length ? 0 : Date.now()};
    setNow(Date.now());
  }, []);

  useEffect(() => {
    let alive = true;
    let timer: ReturnType<typeof setTimeout>;
    const tick = async () => {
      await refresh(false);
      if (alive) timer = setTimeout(tick, 3000);
    };
    void tick();
    const second = setInterval(() => setNow(Date.now()), 1000);
    return () => {
      alive = false;
      ++request.current;
      clearTimeout(timer);
      clearInterval(second);
    };
  }, [refresh]);

  const nextDrop = async () => {
    setDropBusy(true);
    setNotice("");
    try {
      const r = await postData<RunResult & {done?: boolean; step: number; of: number; drops?: number}>("/api/replay/step");
      setNotice(
        r.done
          ? `All ${r.drops} recorded drops are in.`
          : r.status === "partial" ? "Drop incomplete. Retry this step after reviewing the trace."
          : `Drop ${r.step} of ${r.of} processed: ${r.added} files added, ${r.changed} changed, ${r.removed} removed. ${r.open_issues} findings open.`,
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

  const recovery = snap.queue.filter(entry => entry.on_time_recovery);
  const future = snap.queue.filter(entry => entry.option.horizon === "future");
  const other = snap.queue.filter(entry => !entry.on_time_recovery && entry.option.horizon !== "future");
  const renderActions = (entries: BuyerQueueEntry[]) => <ol className="space-y-4">
    {entries.map(entry => <li id={`action-${entry.key}`} tabIndex={-1} key={entry.key}>
      <DecisionView entry={entry} snap={snap} onDone={refresh} onNotice={setNotice}
        current={online === true && !dropBusy && !missing.includes("queue") && !missing.includes("setup")} />
    </li>)}
  </ol>;

  const snapshotTime = editions.map(f => f.details.as_of).find((t): t is string => typeof t === "string");
  const deadline = editions.find((f) => f.deadline)?.deadline ?? null;
  const mock = snap.health?.mockMode ?? true;

  return (
    <div className="mx-auto max-w-[1360px] px-4 pb-16 sm:px-6">
      <TopBar
        setup={snap.setup}
        online={online}
        mock={mock}
        deadline={deadline}
        now={now}
        snapshotTime={snapshotTime}
        retry={snap.health?.lastDrop?.status === "partial" && snap.health?.lastDrop?.mode === "replay"}
        busy={dropBusy}
        replayEnabled={!!snap.health?.replayEnabled}
        onDrop={nextDrop}
      />
      {snap.health?.lastDrop?.mode === "simulation" && <p className="mb-3 border-l-4 border-warn bg-warn-soft px-4 py-2 text-sm font-semibold">
        {snap.health.lastDrop.label}
      </p>}
      {snap.health?.lastDrop?.status === "partial" && <p role="alert" className="mb-4 rounded-md bg-warn-soft p-3">
        Forecast needs review. {snap.health.lastDrop.failures.map(f => f.agent + " failed on " + f.event).join(", ")}.
        {" "}<a className="underline" href={API_URL + "/api/trace?run_id=" + snap.health.lastDrop.run_id}>Open run trace</a>
      </p>}
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
        Update unavailable for {missing.join(", ")}. Showing the last available data.
      </p>}
      {(!missing.includes("forecast") || editions.length > 0) && <Hero editions={editions} />}
      <CaseUpdates snap={snap} openMail={id => { setActiveMail(id); setTab("emails"); }} />

      <div className="mt-6 space-y-6">
        <section aria-labelledby="decide">
          <div className="mb-3 flex items-baseline justify-between gap-4">
            <h2 id="decide" className="text-xl font-bold">
              Decide now
            </h2>
            <span className="text-sm text-muted tabular">
              {snap.queue.length} {snap.queue.length === 1 ? "action" : "actions"}
            </span>
          </div>
          {snap.queue.length === 0 ? (
            <p className="rounded-lg border border-dashed border-rule bg-surface px-5 py-8 text-muted">
              {missing.includes("queue") || online !== true ? "Waiting for current decisions." : "No decisions are waiting for approval."}
            </p>
          ) : (
            <>
              <div className={`grid items-start gap-5 ${future.length && (recovery.length || other.length) ? "lg:grid-cols-2" : ""}`}>
                {(recovery.length > 0 || other.length > 0) && <section aria-label="Packing deadline">
                  <h3 className="mb-2 font-semibold">Packing deadline</h3>
                  {renderActions((recovery.length ? recovery : other).slice(0, 1))}
                </section>}
                {future.length > 0 && <section aria-label="Future supply">
                  <h3 className="mb-2 font-semibold">Future supply</h3>{renderActions(future.slice(0, 1))}
                </section>}
              </div>
              {recovery.length > 1 && <details className="mt-3">
                <summary className="cursor-pointer py-2 font-semibold text-act">More packing options ({recovery.length - 1})</summary>
                {renderActions(recovery.slice(1))}
              </details>}
              {future.length > 1 && <details className="mt-3">
                <summary className="cursor-pointer py-2 font-semibold text-act">More future supply reviews ({future.length - 1})</summary>
                {renderActions(future.slice(1))}
              </details>}
              {other.length > (recovery.length ? 0 : 1) && <details className="mt-2">
                <summary className="cursor-pointer py-2 text-sm font-semibold text-act">Other requests and reviews</summary>
                {renderActions(other.slice(recovery.length ? 0 : 1))}
              </details>}
            </>
          )}
        </section>

        <aside aria-labelledby="agents" className="grid items-start gap-6 lg:grid-cols-2">
          <AgentLanes trace={snap.trace} now={now} onOpenData={() => setShowData(true)} />
          <LiveLog trace={snap.trace} />
        </aside>
      </div>

      <section className="mt-10" aria-label="Details">
        <div role="tablist" className="mb-4 flex gap-1 border-b border-rule">
          {(
            [
              ["findings", `Findings (${snap.issues.filter((i) => i.status !== "resolved").length} open)`],
              ["emails", `Supplier emails (${snap.outbox.length})`],
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
        {tab === "findings" && <Findings issues={snap.issues} />}
        {tab === "emails" && <Emails activeMail={activeMail} outbox={snap.outbox} inbox={snap.inbox} mock={mock} onSent={refresh} />}
        {tab === "risk" && <Risk rows={snap.risk} />}
      </section>
      {showData && <DataDrawer onClose={closeData} />}
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

function CaseUpdates({ snap, openMail }: { snap: Snapshot; openMail: (id: number) => void }) {
  const [all, setAll] = useState(false);
  const decided = snap.decisions.filter(d => d.status === "rejected" || (d.chosen_option != null && ["approved", "executed"].includes(d.status)))
    .sort((a, b) => (b.decided_at ?? "").localeCompare(a.decided_at ?? "") || b.id - a.id);
  if (!decided.length) return null;
  return <section aria-label="Decision follow-up" className="mt-6 rounded-lg border border-rule bg-surface p-5">
    <h2 className="font-bold">Decision follow-up</h2>
    <ul className="mt-3 space-y-3">{(all ? decided : decided.slice(0, 3)).map(d => {
      const option = d.options[d.chosen_option!];
      const sent = snap.outbox.find(o => o.decision_id === d.id);
      const reply = sent && snap.inbox.filter(i => i.outbox_id === sent.id).sort((a,b) => b.id - a.id)[0];
      const state = d.status === "rejected" ? "Rejected" : reply ? replyOutcome(reply)
        : sent?.status === "failed" ? "Send failed. The buyer needs to check the email."
        : sent?.status === "draft" ? "Chaser prepared a draft. Delivery is pending."
        : sent?.status === "sent" ? (snap.health?.mockMode && sent.sent_copy_pending == null
          ? "Simulated request sent. Waiting for a test reply." : "Chaser sent the request. Waiting for the supplier.")
        : sent?.status === "escalated" ? "Chaser escalated the unanswered request to the buyer."
        : option?.actions.some(a => a.type === "human_review") ? `Assigned to ${d.approver || "the buyer"} for internal review.`
        : "Approved. Waiting for the handoff.";
      const edition = snap.forecast.find(f => d.title.startsWith(f.edition + " ·"))?.edition;
      const accepted = sent && snap.inbox.find(i => i.outbox_id === sent.id && i.result?.confirmed_delivery);
      const outcome = accepted && edition ? forecastAfterReply(accepted, snap.forecast, edition) : "";
      const review = snap.issues.find(i => i.type === "recovery_review" && i.evidence.some(e => e.path === `decisions/${d.id}`));
      const priced = reply && reply.result?.supplier_quote ? snap.queue.find(e => e.option.actions.some(a => a.quote_inbox_id === reply.id)) : undefined;
      return <li key={d.id}><strong>{option?.label ?? d.title}</strong>
        <span className="ml-2 text-sm text-muted">#{d.id} · {d.decided_by}</span>
        <p className="text-sm">{state}</p>{outcome && <p className="text-sm font-semibold">{outcome}</p>}
        {review && <details className="text-sm"><summary className="cursor-pointer text-act">Open assigned review #{review.id}</summary>
          <p className="mt-1 font-semibold">{review.impact.note}</p><p>{review.reason}</p><p>Review status · {review.status}</p>
          <a className="text-act underline" href={API_URL + "/api/issues/" + review.id}>View review record</a></details>}
        {priced && <button className="mr-4 text-sm font-semibold text-act underline" onClick={() => {
          const target = document.getElementById("action-" + priced.key);
          const container = target?.closest("details");
          if (container) container.open = true;
          target?.focus();
        }}>Review priced offer</button>}
        {sent && <a className="text-sm text-act underline" href={`#mail-${sent.id}`} onClick={() => openMail(sent.id)}>Open supplier thread</a>}
      </li>;
    })}</ul>
    {decided.length > 3 && <button className="mt-2 text-sm font-semibold text-act" onClick={() => setAll(!all)}>
      {all ? "Show latest three" : `Show all ${decided.length} decisions`}</button>}
  </section>;
}

function TopBar(props: {
  setup: CustomerSetup | null;
  online: boolean | null;
  mock: boolean;
  deadline: string | null;
  snapshotTime?: string;
  retry: boolean;
  now: number;
  busy: boolean;
  replayEnabled: boolean;
  onDrop: () => void;
}) {
  const end = parseTs(props.deadline);
  const left = end && props.now ? end.getTime() - props.now : null;
  const countdown =
    left == null
      ? null
      : left <= 0
        ? "Packing has closed"
        : `${Math.floor(left / 3_600_000)} h ${Math.floor((left % 3_600_000) / 60_000)} min until packing closes`;
  const closes = end?.toLocaleString("en-GB", {
    weekday: "short", hour: "2-digit", minute: "2-digit", timeZone: props.setup?.timezone || "UTC",
  });
  const snapshot = parseTs(props.snapshotTime)?.toLocaleString("en-GB", {
    weekday: "short", hour: "2-digit", minute: "2-digit", timeZone: props.setup?.timezone || "UTC",
  });
  return (
    <header className="flex flex-wrap items-center justify-between gap-4 py-5">
      <div>
        {props.setup?.demo_buyer && (
          <p className="text-sm font-semibold">
            Demo view · {props.setup.demo_buyer} · Procurement buyer
          </p>
        )}
        <h1 className="text-2xl font-bold leading-tight">{props.setup?.customer || "Procurement"} launch control</h1>
        <p className="text-sm text-muted">
          {props.setup?.site ? props.setup.site + " packing line" : "Packing line"}
          {props.setup && (props.setup.autonomy.mode === "control" ? " · Control mode" :
            ` · Auto below ${formatMoney().format(props.setup.autonomy.auto_execute_below_eur)}`)}
          {props.replayEnabled && snapshot && closes ? ` · Packing snapshot ${snapshot} · Close ${closes}` : countdown && (
            <>
              {", "}
              <span className="font-semibold text-ink tabular">{countdown}</span>
              {closes && ` (${closes})`}
            </>
          )}
        </p>
      </div>
      <div className="flex flex-wrap items-center gap-3">
        <span
          title={props.mock ? "MOCK_MODE=true: no AI calls, no real emails" : "Live: real AI calls and emails"}
          className={`rounded px-2 py-1 text-xs font-bold ${props.mock ? "bg-warn-soft text-warn" : "bg-ok-soft text-ok"}`}
        >
          {props.replayEnabled ? "Historical replay" : props.mock ? "Mock mode" : "Live"}
        </span>
        <span className="flex items-center gap-1.5 text-sm text-muted">
          <span
            aria-hidden
            className={`inline-block h-2 w-2 rounded-full ${
              props.online ? "bg-ok" : props.online === false ? "bg-stop" : "bg-rule"
            }`}
          />
          {props.online ? "Backend online" : props.online === false ? "Backend offline" : "Connecting"}
        </span>
        <button
          onClick={props.onDrop}
          disabled={props.busy || !props.online || !props.replayEnabled}
          className="rounded-md bg-ink px-4 py-2 text-sm font-semibold text-white hover:bg-act disabled:cursor-not-allowed disabled:opacity-50"
          title="Resumes the next recorded drop in the dedicated replay database."
        >
          {props.busy ? "Agents are working" : props.retry ? "Retry incomplete drop" : "Play next data drop"}
        </button>
        <Link
          href="/observability"
          className="rounded-md border border-rule bg-surface px-3.5 py-2 text-sm font-semibold text-ink hover:border-act hover:text-act"
          title="Open dedicated AI Observability dashboard with per-agent visual reports"
        >
          AI Observability
        </Link>
      </div>
    </header>
  );
}

function Hero({ editions }: { editions: Forecast[] }) {
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
          <div key={f.edition} className="bg-surface px-5 py-4">
            <p className={`figure text-[clamp(40px,5vw,64px)] ${short ? "text-stop" : "text-ok"}`}>
              {f.shortfall == null ? "—" : fmt(f.shortfall)}
            </p>
            <p className="mt-2 text-lg font-semibold">
              {f.edition} copies {f.shortfall == null ? "awaiting forecast" : short ? "projected short by close" : "short · on track"}
            </p>
            <p className="mt-1 text-sm text-muted tabular">
              {fmt(f.still_to_pack)} still to pack
              {f.still_to_pack != null && f.shortfall != null && `, ${fmt(Math.max(0, f.still_to_pack - (f.shortfall ?? 0)))} of them by close`}
              {f.limiting_part && (
                <>
                  , held up by <span className="font-semibold text-ink">{f.limiting_part}</span>
                </>
              )}
            </p>
          </div>
        );
      })}
    </section>
  );
}

const formatMoney = (unit = "EUR") => new Intl.NumberFormat("en-IE", {
  style: "currency", currency: unit, minimumFractionDigits: 0, maximumFractionDigits: 2,
});

function DecisionView({ entry, snap, current, onDone, onNotice }: {
  entry: BuyerQueueEntry; snap: Snapshot; current: boolean; onDone: () => Promise<void>; onNotice: (message: string) => void;
}) {
  const [busy, setBusy] = useState(false);
  const option = entry.option;
  const buyer = snap.setup?.demo_buyer;
  const approvalCurrency = formatMoney(option.currency || "EUR");
  const assigned = option.approvers ?? [];
  const ownsAction = !!buyer && assigned.length === 1 && assigned[0] === buyer;
  const cost = option.cost_eur;
  const value = option.approval_value_eur;
  const card = snap.decisions.find(d => d.id === entry.decision_id);
  const comparison = entry.freight_comparison;
  const alternative = comparison?.alternatives[0];
  const baseline = card?.options.find(o => o.description === "baseline");
  const action = option.actions[0];
  const button = option.button_label || (action?.type === "human_review" ? "Open review" :
    action?.quote_inbox_id ? "Authorize quote" : action?.type === "email" ? "Send supplier request" : "Approve");
  const role = option.approval_roles?.join(" + ") || "Unassigned role";
  const issueIds = [...new Set(entry.sources.flatMap(s => s.issue_ids))];
  const matrix = snap.setup?.approval_matrix ?? [];
  const band = value == null ? -1 : matrix.findIndex(r => r.below_eur == null || value < r.below_eur);
  const rule = matrix[band];
  const lower = matrix[band - 1]?.below_eur ?? 0;
  const deadline = parseTs(entry.decide_by)?.toLocaleString("en-GB", {
    weekday: "short", hour: "2-digit", minute: "2-digit", timeZone: snap.setup?.timezone || "UTC",
  });

  const decide = async (verb: "approve" | "reject") => {
    if (!current || !ownsAction) return;
    setBusy(true);
    onNotice("");
    try {
      await postData(`/api/decisions/${entry.decision_id}/${verb}`, {
        by: buyer, option: entry.option_index, version: entry.version,
      });
      onNotice(verb === "reject" ? "Decision rejected." : action?.type === "human_review"
        ? `Internal review authorized. See decision follow-up for the owner and record.`
        : "Supplier request authorized. See decision follow-up for delivery status.");
    } catch (e) {
      onNotice(e instanceof ApiHttpError && e.status === 409
        ? `${e.message.replace(/[.!]$/, "")}. Check the current buyer queue before acting again.` : (e as Error).message);
    } finally {
      await onDone();
      setBusy(false);
    }
  };

  return <article className={`rounded-lg border border-rule border-t-[3px] bg-surface ${option.horizon === "future" ? "border-t-warn" : "border-t-act"}`}>
    <div className="px-5 pt-4">
      <p className="text-sm text-muted">{entry.edition} · {entry.part || "Packing capacity"}</p>
      <h3 className="mt-1 text-lg font-bold leading-snug">{option.label}</h3>
      {option.exposure ? <ExposureSummary exposure={option.exposure} /> :
        <p className="mt-2 text-sm">{entry.next_step}</p>}
      {action?.quote_inbox_id != null && <p className="mt-1 text-sm font-semibold text-act">Quote received · Awaiting authorization</p>}
      <dl className="my-4 flex flex-wrap gap-x-7 gap-y-3 text-sm tabular">
        <div><dt className="text-muted">{option.horizon === "future" ? "Time horizon" : "Conditional gain"}</dt><dd className="font-semibold">
          {option.horizon === "future" ? "Future replenishment" : option.copies_gained == null ? "Unknown" : `Up to ${fmt(option.copies_gained)} copies`}</dd></div>
        <div><dt className="text-muted">{option.horizon === "future" ? "Future sourcing cost" : "Cost"}</dt><dd>{cost == null ? "Unknown" : approvalCurrency.format(cost)}</dd></div>
        <div><dt className="text-muted">{option.horizon === "future" ? "Spend authorized" : "Approval value"}</dt><dd>{value == null ? "Unknown" : approvalCurrency.format(value)}</dd></div>
        <div><dt className="text-muted">Assigned to</dt><dd>{assigned.join(" + ") || "Pending assignment"} · {role}</dd></div>
      </dl>
      {comparison && comparison.fee_per_additional_copy != null && <p className="mb-2 text-sm">
        {alternative && comparison.preferred && <strong>Best on-time gain at the lowest fee for that gain. </strong>}
        The fee works out to {approvalCurrency.format(comparison.fee_per_additional_copy)} per additional copy if the delivery is confirmed on time.
        {alternative && ` ${alternative.label} would cost ${approvalCurrency.format(alternative.fee)} for up to ${fmt(alternative.gain)} copies.`}
      </p>}
      {entry.shared_shipment && <p className="mb-2 text-sm text-muted">
        Other options use this shipment ({entry.shared_shipment}). Count its copy gain once.
      </p>}
      <p className="text-sm"><strong>Still needed</strong> · {entry.requirements.join(" · ")}</p>
    </div>
    <div className="mt-4 flex flex-wrap items-center gap-3 border-t border-rule px-5 py-3 text-sm">
      {ownsAction ? <button disabled={!current || busy} onClick={() => decide("approve")}
        className="rounded-md bg-act px-4 py-2 font-semibold text-white hover:bg-ink disabled:opacity-50">{button}</button>
        : <strong>{assigned.length ? `Awaiting ${assigned.join(" + ")}` : "Awaiting assignment"}{assigned.length > 0 && assigned.join(" + ") !== role && ` · ${role}`}</strong>}
      {deadline && <span className="text-muted">Decide by {deadline} {snap.setup?.timezone || "UTC"}</span>}
    </div>

    <details className="border-t border-rule px-5 py-3 text-sm">
      <summary className="cursor-pointer font-semibold text-act">Why? · {issueIds.length} linked {issueIds.length === 1 ? "finding" : "findings"}</summary>
      {option.exposure ? <ExposureEvidence exposure={option.exposure} /> : <>
        <p className="mt-3">{option.risk_note}</p>
        {baseline?.remaining_shortfall != null && <section className="mt-3">
          <h4 className="font-bold">Compare by packing deadline</h4>
          <table className="mt-1 w-full text-left text-sm">
            <thead><tr className="text-muted"><th>Option</th><th className="text-right">Copies still short</th></tr></thead>
            <tbody>{[baseline, option, ...(card?.options ?? []).filter(o => o !== baseline &&
              o !== card?.options[entry.option_index] && o.remaining_shortfall != null)].map((o, i) =>
              <tr className="border-t border-rule" key={i}><td className="py-1">{o.label}
                {o.available === false && " · unavailable"}</td>
                <td className="text-right tabular">{o.remaining_shortfall == null ? "Unknown" : fmt(o.remaining_shortfall)}</td></tr>)}</tbody>
          </table><p className="text-muted">Each alternative assumes its requirements are met.</p>
        </section>}
      </>}
      {option.review_brief && <p className="mt-2 font-medium">{option.review_brief}</p>}
      {option.horizon === "future" && <p className="mt-2">Opening this review assigns the work to {assigned.join(" + ") || role}. Any purchase requires a separate decision.</p>}
      {option.horizon !== "future" && rule && value != null && <p className="mt-2">
        {approvalCurrency.format(value)} falls in the {rule.approvers.join(" + ")} approval band
        {" "}from {approvalCurrency.format(lower)}{rule.below_eur == null ? " upwards" : ` to below ${approvalCurrency.format(rule.below_eur)}`}.
        {option.actions.some(a => a.quote_inbox_id != null) && " This includes the original PO and the extra quoted charge."}
      </p>}
      <details className="my-3"><summary className="cursor-pointer">Linked decisions and findings</summary><ul className="mt-2 space-y-2">{entry.sources.map(source => {
        const card = snap.decisions.find(d => d.id === source.decision_id);
        return <li key={`${source.decision_id}-${source.option_index}`}>
          <a className="text-act underline" href={`${API_URL}/api/decisions/${source.decision_id}`}>Decision #{source.decision_id}</a>
          {" · "}{card?.options[source.option_index]?.label ?? `Option ${source.option_index + 1}`}
          {source.related_review && " · Related internal review"}
          {source.issue_ids.map(id => <div key={id}><a className="text-act underline" href={`${API_URL}/api/issues/${id}`}>Finding #{id}</a>
            {" · "}{snap.issues.find(i => i.id === id)?.summary}</div>)}
        </li>;
      })}</ul></details>
      {snap.decisions.find(d => d.id === entry.decision_id)?.options.some(o => o.available === false) &&
        <details className="mb-2"><summary className="cursor-pointer">Options ruled out</summary>
          <ul className="mt-2 space-y-2">{snap.decisions.find(d => d.id === entry.decision_id)?.options
            .filter(o => o.available === false).map((o, index) => <li key={index}>
              <strong>{o.label}</strong>{o.copies_gained != null && ` · ${fmt(o.copies_gained)} additional copies`}
              <p>{o.risk_note}</p>
            </li>)}</ul>
        </details>}
      <details><summary className="cursor-pointer">Source evidence ({entry.evidence.length})</summary>
        <ul className="mt-2 space-y-1">{entry.evidence.map((e,i) => <li key={i} className="break-words">
          {e.source_kind && <strong>{e.source_kind} · </strong>}
          {/^https?:\/\//i.test(e.path) ? <a className="underline" href={e.path}>{e.path}</a> : e.path} · {e.locator} {e.quote && `· ${e.quote}`}</li>)}</ul>
      </details>
      {ownsAction && snap.decisions.find(d => d.id === entry.decision_id)?.recommended === entry.option_index &&
        <button className="mt-3 font-semibold text-act" disabled={!current || busy} onClick={() => decide("reject")}>
          Reject source decision #{entry.decision_id}</button>}
    </details>
  </article>;
}

function AgentLanes({ trace, now, onOpenData }: { trace: AgentLogEntry[]; now: number; onOpenData: () => void }) {
  const stats = useMemo(() => {
    const out = new Map<string, { last?: AgentLogEntry; count: number; errors: number }>();
    for (const e of trace) {
      const s = out.get(e.agent) ?? { count: 0, errors: 0 };
      // Show the newest real step, not the internal "handle:<event>" hand-off lines.
      if (!s.last && !e.action.startsWith("handle:") && e.action !== "unhandled") s.last = e;
      s.count += 1;
      if (e.action === "error") s.errors += 1;
      out.set(e.agent, s);
    }
    return out;
  }, [trace]);
  const max = Math.max(1, ...[...stats.values()].map((s) => s.count));

  return (
    <section aria-labelledby="agents">
      <h2 id="agents" className="mb-3 text-xl font-bold">
        Agents at work
      </h2>
      <ul className="divide-y divide-rule rounded-lg border border-rule bg-surface">
        {AGENTS.map((a) => {
          const s = stats.get(a.id);
          const ts = parseTs(s?.last?.ts);
          const active = ts && now ? now - ts.getTime() < 15_000 : false;
          return (
            <li key={a.id} className="transition hover:bg-dock/50">
              <div className="flex items-center gap-2 px-4 pt-3">
                <span
                  aria-hidden
                  className={`inline-block h-2.5 w-2.5 rounded-full ${active ? "beat" : ""}`}
                  style={{ background: a.color, opacity: s ? 1 : 0.3 }}
                />
                <span className="font-semibold">{a.name}</span>
                {a.id === "watcher" && (
                  <button
                    onClick={onOpenData}
                    className="rounded border border-rule px-2 py-0.5 text-xs font-semibold text-act hover:bg-act-soft"
                    title="Open the data the Watcher fetched, cleaned by Ingest, with SAP table and field names"
                  >
                    SAP data
                  </button>
                )}
                <Link
                  href={`/observability?agent=${a.id}`}
                  className="rounded border border-rule px-2 py-0.5 text-xs font-semibold text-act hover:bg-act-soft"
                  title="View agent trace and observability report"
                >
                  Report →
                </Link>
                <span className="ml-auto text-xs text-muted tabular">
                  {s ? ago(s.last?.ts, now) : "idle"}
                  {s && s.errors > 0 && <span className="ml-2 font-semibold text-stop">{s.errors} errors</span>}
                </span>
              </div>
              <Link href={`/observability?agent=${a.id}`} className="block px-4 pb-3 pt-1">
                <p className="text-xs text-muted">{a.job}</p>
                {s?.last && (
                  <p className="mt-1 truncate text-sm" title={s.last.detail}>
                    {s.last.detail || s.last.action}
                  </p>
                )}
                <div className="mt-2 h-1 rounded bg-dock" aria-hidden>
                  <div className="h-1 rounded" style={{ width: `${((s?.count ?? 0) / max) * 100}%`, background: a.color }} />
                </div>
              </Link>
            </li>
          );
        })}
      </ul>
    </section>
  );
}

function LiveLog({ trace }: { trace: AgentLogEntry[] }) {
  const rows = trace.filter((e) => !e.action.startsWith("handle:") && e.action !== "unhandled").slice(0, 40);
  return (
    <section aria-labelledby="log">
      <h2 id="log" className="mb-3 text-xl font-bold">
        What just happened
      </h2>
      <ol className="max-h-[420px] overflow-y-auto rounded-lg border border-rule bg-surface text-sm">
        {rows.length === 0 && <li className="px-4 py-6 text-muted">The agents have not done anything yet.</li>}
        {rows.map((e) => (
          <li key={e.id} className="flex gap-3 border-b border-rule px-4 py-2 last:border-b-0">
            <span className="shrink-0 text-xs text-muted tabular">{clock(e.ts)}</span>
            <span className="min-w-0">
              <span className="font-semibold" style={{ color: agentColor(e.agent) }}>
                {AGENTS.find((a) => a.id === e.agent)?.name ?? e.agent}
              </span>{" "}
              <span className={e.action === "error" ? "font-semibold text-stop" : "text-muted"}>
                {e.action.replaceAll("_", " ")}
              </span>
              <span className="block break-words">{e.detail}</span>
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
            <th className="px-4 py-2 font-semibold">How bad</th>
            <th className="px-4 py-2 font-semibold">What we found</th>
            <th className="px-4 py-2 font-semibold">Found by</th>
            <th className="px-4 py-2 font-semibold">Status</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((i) => {
            const sev = SEVERITY[i.severity] ?? SEVERITY[1];
            return (
              <tr key={i.id} className={`border-b border-rule last:border-b-0 ${i.status === "resolved" ? "opacity-50" : ""}`}>
                <td className="px-4 py-2 align-top">
                  <span className={`whitespace-nowrap rounded px-2 py-0.5 text-xs font-semibold ${sev.cls}`}>{sev.label}</span>
                </td>
                <td className="px-4 py-2 align-top">
                  {i.summary}
                  {i.evidence[0]?.path && <span className="block text-xs text-muted">{i.evidence[0].path}</span>}
                </td>
                <td className="px-4 py-2 align-top capitalize">{i.raised_by}</td>
                <td className="px-4 py-2 align-top capitalize">{i.status}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function Emails({ activeMail, outbox, inbox, mock, onSent }: { activeMail: number | null; outbox: OutboxMessage[]; inbox: InboxMessage[]; mock: boolean; onSent: () => void }) {
  if (outbox.length === 0) return <p className="text-muted">No supplier emails yet. The Chaser writes one when a finding needs an answer.</p>;
  return (
    <ul className="space-y-3">
      {outbox.map((m) => (
        <li id={`mail-${m.id}`} key={m.id}>
          <EmailView key={String(activeMail === m.id)} focus={activeMail === m.id} m={m} replies={inbox.filter((r) => r.outbox_id === m.id)} mock={mock} onSent={onSent} />
        </li>
      ))}
    </ul>
  );
}

function EmailView({ focus, m, replies, mock, onSent }: { focus: boolean; m: OutboxMessage; replies: InboxMessage[]; mock: boolean; onSent: () => void }) {
  const [open, setOpen] = useState(focus);
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
            placeholder="Confirm the order line, revision, quantity and delivery time."
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
  if (typeof res.review === "string" && res.review.startsWith("Duplicate of inbox/"))
    return "Duplicate reply. The original evidence and forecast still apply.";
  if (typeof res.review === "string") return `Held for review: ${res.review}`;
  const advice = (res.confirmed_delivery ?? res.supplier_quote) as Record<string, unknown> | undefined;
  if (advice) {
    const when = parseTs(String(advice.arrival))?.toLocaleString("en-GB", {
      weekday: "short", day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", timeZone: "UTC",
    });
    return res.supplier_quote
      ? `Ingest recorded a quote for ${fmt(Number(advice.quantity))} pcs. The Planner can now compare the offer.`
      : `Ingest confirmed ${fmt(Number(advice.quantity))} pcs, revision ${String(advice.revision)}, due ${when} UTC. The Planner checks dock release and packing time.`;
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
