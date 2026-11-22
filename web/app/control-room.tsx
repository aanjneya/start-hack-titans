"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, memo, useCallback, useEffect, useId, useMemo, useRef, useState } from "react";
import {
  ago, API_URL, ApiHttpError, clock, eur, fmt, getData, parseTs, postData,
  type DecisionCard, type Forecast, type HealthCheckResponse, type InboxMessage,
  type Issue, type OutboxMessage, type RiskScore,
} from "@/lib/api";
import type { ActionRun, BuyerActivity, BuyerQueueEntry, CustomerSetup, RunResult } from "../../API_CONTRACT";
import { ExposureEvidence, ExposureSummary } from "./supply-exposure";
import { emailDeliveries, OperationsSummary, PackingOutlook, RecoveryOutlook, SupplierRiskWatch } from "./supply-overview";
import DataDrawer from "./data-drawer";
import { actionLine, entryDetail, humanizeAction } from "./observability/agent-meta";
import { RiskBoard, SeverityLabel } from "./risk-board";
import History from "./decision-history";
import SampleTiming from "./sample-timing";
import CheckSignals from "./check-signals";
import BuyerUpdates from "./buyer-updates";
import { SourceList, SourcePassage } from "./decision-source";
import { formatMoney, OrderCalculation } from "./approval-details";
import { actionHref, findingHref, followupHref, mailHref } from "./buyer-links";

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
  trace: BuyerActivity[];
};

const EMPTY: Snapshot = { queue: null, setup: null, health: null, forecast: [], decisions: [], issues: [], outbox: [], inbox: [], risk: [], trace: [] };

const AGENTS: { id: string; name: string; job: string; color: string }[] = [
  { id: "watcher", name: "Watcher", job: "Watches the supplier portal for new files", color: "#5a6478" },
  { id: "ingest", name: "Ingest", job: "Reads every file, scan and reply into one truth", color: "#1d5fb4" },
  { id: "reconciler", name: "Reconciler", job: "Checks the sources against each other", color: "#3b6f8f" },
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

function runWarning(run: ActionRun) {
  if (run.status !== "partial") return "";
  const steps = [...new Set(run.failures.map(f => `${f.agent} on ${f.event}`))].join(", ");
  return `Agent work needs review (${steps}). Open the decision history for the trace.`;
}


async function loadSnapshot(health: HealthCheckResponse): Promise<{ snap: Snapshot; missing: string[] }> {
  const [forecast, decisions, issues, outbox, inbox, risk, trace, setup, queue] = await Promise.allSettled([
    getData<Forecast[]>("/api/forecast?current=true"),
    getData<DecisionCard[]>("/api/decisions?for_buyer=true"),
    getData<Issue[]>("/api/issues"),
    getData<OutboxMessage[]>("/api/outbox"),
    getData<InboxMessage[]>("/api/inbox?with_forecast=true"),
    getData<RiskScore[]>("/api/risk?current=true"),
    getData<BuyerActivity[]>("/api/buyer-activity"),
    getData<CustomerSetup>("/api/setup"),
    getData<BuyerQueueEntry[]>("/api/buyer-queue"),
  ]);
  const val = <T,>(r: PromiseSettledResult<T>, fallback: T) => (r.status === "fulfilled" ? r.value : fallback);
  return {
    missing: Object.entries({forecast, decisions, issues, outbox, inbox, risk, trace, setup, queue})
      .filter(([, response]) => response.status === "rejected").map(([name]) => name),
    snap: {
      setup: val(setup, null),
      queue: val(queue, null),
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

const buyerViews = {
  overview: "Supply chain control room",
  findings: "Findings",
  emails: "Supplier email",
  activity: "Activity",
  risk: "External risk signals",
} as const;

function recordId(raw: string | null) {
  if (!raw || !/^[1-9][0-9]*$/.test(raw)) return null;
  const id = Number(raw);
  return Number.isSafeInteger(id) ? id : null;
}

export default function ControlRoom() {
  return <Suspense fallback={<p className="p-6 text-sm text-muted">Loading buyer workspace…</p>}><BuyerWorkspace /></Suspense>;
}

function BuyerWorkspace() {
  const params = useSearchParams();
  const router = useRouter();
  const rawView = params.get("view");
  const view = rawView && Object.hasOwn(buyerViews, rawView) ? rawView as keyof typeof buyerViews : "overview";
  const actionKey = view === "overview" ? params.get("action") : null;
  const followupId = view === "overview" ? recordId(params.get("followup")) : null;
  const threadParam = view === "emails" ? params.get("mail") : null;
  const findingParam = view === "findings" ? params.get("finding") : null;
  const activeMail = recordId(threadParam);
  const activeFinding = recordId(findingParam);
  const [snap, setSnap] = useState<Snapshot>(EMPTY);
  const describeReply = useCallback((message: InboxMessage) => replyOutcome(message, snap.setup?.timezone), [snap.setup?.timezone]);
  const [missing, setMissing] = useState<string[]>([]);
  const [online, setOnline] = useState<boolean | null>(null);
  const [now, setNow] = useState<number>(0);
  const [dropBusy, setDropBusy] = useState(false);
  /** When the running drop started: agents that log after this are working on it. */
  const [dropStartedAt, setDropStartedAt] = useState<number | null>(null);
  const [notice, setNotice] = useState<string>("");
  const [playedRun, setPlayedRun] = useState("");
  const announceAction = useCallback((message: string) => {
    setPlayedRun("");
    setNotice(message);
  }, []);
  const openMail = (id: number) => router.push(mailHref(id));
  const openAction = (key: string) => router.push(actionHref(key), {scroll:false});
  const closeAction = () => router.replace("/", {scroll:false});

  useEffect(() => {
    const resolveLegacyMail = () => {
      const id = recordId(/^#mail-([0-9]+)$/.exec(window.location.hash)?.[1] ?? null);
      if (id) router.replace(mailHref(id));
    };
    resolveLegacyMail();
    window.addEventListener("hashchange", resolveLegacyMail);
    return () => window.removeEventListener("hashchange", resolveLegacyMail);
  }, [router]);

  // On first load, Tab starts at the skip link. Within the workspace it follows the selected record.
  const destination = params.toString();
  const lastDestination = useRef(destination);
  useEffect(() => {
    if (lastDestination.current === destination) return;
    lastDestination.current = destination;
    if (actionKey || followupId) return;
    const target = document.getElementById("buyer-record-heading") ?? document.getElementById("buyer-page-heading");
    target?.focus({preventScroll:true});
  }, [destination, actionKey, followupId]);
  const [showData, setShowData] = useState(false);
  const closeData = useCallback(() => setShowData(false), [setShowData]);

  const lastSnapshot = useRef({version: "", fetched: 0});
  const request = useRef(0);
  const followDecision = useCallback((id: number) => { router.replace(followupHref(id), {scroll:false}); }, [router]);
  const nextRequest = useCallback(() => ++request.current, []);
  const refresh = useCallback(async (force = true) => {
    const id = nextRequest();
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
    // Force a full read after user actions and periodically for unlogged external changes.
    if (!force && health.activity_version && health.activity_version === lastSnapshot.current.version
      && Date.now() - lastSnapshot.current.fetched < 30_000) return;
    const { snap: next, missing: failed } = await loadSnapshot(health);
    if (id !== request.current) return;
    setOnline(true);
    setMissing(failed);
    setSnap(previous => ({...next, ...Object.fromEntries(
      failed.map(name => [name, previous[name as keyof Snapshot]]))}));
    lastSnapshot.current = {version: health.activity_version ?? "", fetched: failed.length ? 0 : Date.now()};
    setNow(Date.now());
  }, [nextRequest]);

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
      nextRequest();
      clearTimeout(timer);
      clearInterval(second);
    };
  }, [refresh, nextRequest]);

  const followupReady = followupId != null && snap.decisions.some(d => d.id === followupId &&
    (d.status === "rejected" || (d.chosen_option != null && ["approved", "executed", "outdated"].includes(d.status))));
  useEffect(() => {
    if (!followupReady || followupId == null) return;
    const followup = document.getElementById(`followup-${followupId}`);
    const review = followup?.querySelector<HTMLDetailsElement>("details[data-assigned-review]");
    if (review) review.open = true;
    followup?.focus();
  }, [followupId, followupReady]);

  const nextDrop = async () => {
    setDropStartedAt(Date.now());
    setDropBusy(true);
    setNotice("");
    setPlayedRun("");
    try {
      const r = await postData<RunResult & {done?: boolean; step: number; of: number; drops?: number}>("/api/replay/step");
      if (r.status === "completed") setPlayedRun(r.run_id);
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

  // Product IDs also distinguish two customers' SKUs with the same edition label.
  const editions = useMemo(() => {
    const seen = new Map<string, Forecast>();
    for (const f of snap.forecast) if (!seen.has(f.product_id)) seen.set(f.product_id, f);
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

  const queue = snap.queue ?? [];
  const riskCheck = snap.health?.lastRiskCheck;
  const marketRun = riskCheck?.trigger === "risk.refresh.requested" ? riskCheck.run_id : null;
  const latestDrop = !notice && snap.health?.lastDrop?.status === "completed" ? snap.health.lastDrop.run_id : null;
  // One line for a supplier grade change from this drop or signal check, or for the newest supplier quote.
  const updated = queue.find(entry => entry.option.horizon === "future" && !entry.option.followup_for &&
    entry.latest_grade_change && [playedRun, marketRun, latestDrop].includes(entry.latest_grade_change.run_id));
  const newestQuote = snap.inbox.find(reply => reply.processed && reply.result.supplier_quote);
  const quoted = newestQuote && queue.find(entry => entry.option.available !== false &&
    entry.option.actions.some(action => action.quote_inbox_id === newestQuote.id));
  const current = online === true && !dropBusy && !missing.includes("queue") && !missing.includes("setup");

  const snapshotTime = editions.map(f => f.details?.as_of).find((t): t is string => typeof t === "string");
  const deadline = editions.find((f) => f.deadline)?.deadline ?? null;
  const mock = snap.health?.mockMode ?? true;
  const deliveries = useMemo(() => emailDeliveries(snap.outbox, snap.trace, !!snap.health?.replayEnabled),
    [snap.outbox, snap.trace, snap.health?.replayEnabled]);

  const queueReady = current && !missing.includes("decisions") && !missing.includes("issues");
  const selectedAction = actionKey && queueReady && queue.some(entry => entry.key === actionKey) ? actionKey : null;
  const selectedMailExists = activeMail != null && snap.outbox.some(mail => mail.id === activeMail);

  return (
    <div className="mx-auto min-h-screen max-w-[1360px] px-4 pb-16 sm:px-6">
      <TopBar
        title={buyerViews[view]}
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
        updates={snap.setup?.demo_buyer && <BuyerUpdates key={snap.setup.customer + "/" + snap.setup.demo_buyer}
          setup={snap.setup} activity={snap.trace} queue={queue} inbox={snap.inbox} decisions={snap.decisions} describeReply={describeReply} onMail={openMail}
          onAction={key => {
            const parent = queue.find(e => e.key === key)?.option.followup_for;
            if (parent) router.push(followupHref(parent));
            else openAction(key);
          }} />}
      />
      <main>
      {snap.health?.lastDrop?.mode === "simulation" && <p className="mb-3 border-l-4 border-warn bg-warn-soft px-4 py-2 text-sm font-semibold">
        {snap.health.lastDrop.label}
      </p>}
      {snap.health?.lastDrop?.status === "partial" && <p role="alert" className="mb-4 rounded-md bg-warn-soft p-3">
        Forecast needs review. {snap.health.lastDrop.failures.map(f => f.agent + " failed on " + f.event).join(", ")}.
        {" "}<a className="underline" target="_blank" rel="noreferrer" href={API_URL + "/api/trace?run_id=" + snap.health.lastDrop.run_id}>Run trace (JSON)</a>
      </p>}
      {riskCheck && (Object.keys(riskCheck.unavailable).length > 0 || !!riskCheck.failures?.length) && <p role="alert" className="mb-4 rounded-md bg-warn-soft p-3">
        Risk check incomplete · {Object.keys(riskCheck.unavailable).length ? Object.keys(riskCheck.unavailable).join(", ") + " unavailable."
          : riskCheck.failures?.map(f => f.agent + " failed on " + f.event).join(", ") + "."}
        {riskCheck.grades_changed == null && " The previous supplier grades still apply."}{" "}
        <a className="font-semibold underline" target="_blank" rel="noreferrer" href={API_URL + "/api/trace?run_id=" + riskCheck.run_id}>Check trace (JSON)</a>
      </p>}
      {(notice || (view === "overview" && (updated || quoted))) && (
        <p role="status" className="mb-4 rounded-md border border-rule bg-surface px-4 py-2 text-sm">
          {updated?.latest_grade_change ? <>{updated.latest_grade_change.supplier} moved from {updated.latest_grade_change.previous} to {updated.latest_grade_change.current} {marketRun === updated.latest_grade_change.run_id ? "after a signal refresh" : "in this drop"}.
            {" "}<button className="font-semibold text-act underline" onClick={() => openAction(updated.key)}>See the action</button>
            </> : notice || (quoted && <>Supplier {quoted.option.description} quote received
              {quoted.option.cost_eur != null && <> · <strong>{formatMoney().format(quoted.option.cost_eur)}</strong> fee</>}
              {quoted.option.approval_basis && <> · <strong>{formatMoney().format(quoted.option.approval_basis.total_eur)}</strong> amended PO</>}.
              {quoted.option.approvers?.length ? ` Assigned to ${quoted.option.approvers.join(" + ")}. ` : " "}
              <button className="font-semibold text-act underline" onClick={() => openAction(quoted.key)}>See approval route</button></>)} {notice.includes("decision follow-up") && <Link className="font-semibold text-act underline" href={followupId ? followupHref(followupId) : "/#decision-follow-up"}>View follow-up</Link>}
        </p>
      )}
      {online === false && (
        <p role="alert" className="mb-4 rounded-md bg-stop-soft px-4 py-3 text-sm text-stop">
          The buyer workspace is offline. Saved information may be out of date.{" "}
          <button className="font-semibold underline" onClick={() => void refresh()}>Retry connection</button>
        </p>
      )}

      {missing.length > 0 && <p role="alert" className="mb-4 text-sm text-warn">
        Couldn&apos;t refresh {missing.join(", ")}. Showing the last data.
      </p>}
      {actionKey && queueReady && !selectedAction && <p role="status" className="my-4 rounded-md bg-warn-soft p-3">
        This action is no longer in the current buyer queue. <Link href="/" className="font-semibold underline">Review current risks</Link>
      </p>}
      {followupId && online && !missing.includes("decisions") && !followupReady && <p role="status" className="my-4 text-sm text-muted">
        {snap.decisions.some(d => d.id === followupId && d.status === "proposed") ? "Waiting for a recorded decision." : "This decision follow-up is unavailable."}
      </p>}
      {view === "overview" && <div className="mt-5 grid grid-cols-[minmax(0,1fr)] items-start gap-6 lg:grid-cols-[minmax(0,1fr)_300px] xl:gap-8">
        <div className="min-w-0 space-y-5">
          <RiskBoard key={actionKey ?? "overview"}
            current={current}
            decisions={snap.decisions}
            issues={snap.issues}
            risk={snap.risk}
            forecast={snap.forecast}
            setup={snap.setup}
            queue={snap.queue}
            focus={selectedAction ? {key:selectedAction, n:1} : null}
            onActionClose={closeAction}
            emptyText={missing.includes("decisions") || online !== true ? "Waiting for current decisions." : "No decisions are waiting for approval."}
            onChanged={refresh}
            renderAction={(key) => {
              const entry = queue.find((e) => e.key === key);
              return entry && (
                <DecisionView entry={entry} snap={snap} current={current} onDone={refresh} onNotice={announceAction} onDecided={followDecision} />
              );
            }}
            renderDecision={(card) => (
              <CardDetails
                setup={snap.setup}
                card={card}
                similar={decisionGroups.find((g) => g.card.id === card.id)?.similar ?? []}
                onDone={refresh}
              />
            )}
          />

          <SupplierRiskWatch scores={snap.risk} queue={snap.queue} waiting={online !== true || missing.includes("risk")} />
          <div className="grid gap-3 md:grid-cols-2">
            <PackingOutlook editions={editions} waiting={online !== true || missing.includes("forecast")} replayEnabled={!!snap.health?.replayEnabled} />
            <RecoveryOutlook queue={snap.queue} waiting={online !== true || missing.includes("queue")} />
            <OperationsSummary issues={snap.issues} inbox={snap.inbox} outbox={snap.outbox} deliveries={deliveries}
              issuesReady={online === true && !missing.includes("issues")} mailReady={online === true && !missing.includes("outbox")}
              repliesReady={online === true && !missing.includes("inbox")} />
          </div>
        </div>

        <aside aria-labelledby="agents" className="space-y-8">
          <AgentLanes
            registered={snap.health?.agents}
            online={online}
            traceReady={!missing.includes("trace")}
            trace={snap.trace}
            now={now}
            busy={dropBusy}
            since={dropStartedAt}
            onOpenData={() => setShowData(true)}
          />
          <CaseUpdates selected={followupId} snap={snap} deliveries={deliveries} refresh={refresh} current={online === true && !dropBusy && !missing.length}
            onAction={openAction} />
        </aside>
      </div>}
      {view === "overview" && <section className="mt-6" aria-labelledby="recent-activity">
        <div className="mb-3 flex items-baseline justify-between gap-3">
          <h2 id="recent-activity" className="text-xl font-semibold">Recent activity</h2>
          <Link href="/?view=activity" className="text-sm font-semibold text-act hover:underline">See all activity</Link>
        </div>
        {online === true && !missing.includes("trace") ? <LiveLog trace={snap.trace} limit={3} /> : <p className="text-sm text-muted">Waiting for agent activity.</p>}
      </section>}
      {view !== "overview" && <section className="mt-5 space-y-4" aria-labelledby="buyer-page-heading">
        {view === "activity" && (snap.trace.length || (online && !missing.includes("trace")) ?
          <LiveLog trace={snap.trace} /> : <p className="text-muted">Waiting for agent activity.</p>)}
        {view === "findings" && (findingParam != null ?
          <FindingDetail key={findingParam} id={activeFinding} issue={snap.issues.find(issue => issue.id === activeFinding)} queue={current ? queue : []} cards={snap.decisions} />
          : snap.issues.length || (online && !missing.includes("issues")) ? <Findings issues={snap.issues} /> : <p className="text-muted">Waiting for findings.</p>)}
        {view === "emails" && <>
          {threadParam != null && <Link className="text-sm font-semibold text-act hover:underline" href="/?view=emails">← All supplier email</Link>}
          {threadParam != null && !selectedMailExists
            ? <p role="status" className="text-muted">{activeMail == null || (online && !missing.includes("outbox")) ? "This supplier thread is unavailable in the current mailbox." : "Waiting for the supplier thread."}</p>
            : (snap.outbox.length || snap.inbox.length || (online && !missing.includes("outbox") && !missing.includes("inbox")))
              ? <Emails deliveries={deliveries} activeMail={activeMail} snap={snap} mock={mock} canRecord={online === true && !missing.includes("outbox") && !missing.includes("inbox")} onSent={refresh} />
              : <p className="text-muted">Waiting for the supplier mailbox.</p>}
        </>}
        {view === "risk" && <>
          <Link href="/observability?agent=risk" className="inline-block text-sm font-semibold text-act hover:underline">← Risk ratings</Link>
          <SignalRefresh reading={riskCheck} scores={snap.risk} timezone={snap.setup?.timezone} online={online === true} mock={mock} refresh={refresh} />
        </>}
      </section>}
      </main>
      {showData && <DataDrawer onClose={closeData} />}
    </div>
  );
}

function forecastAfterReply(reply: InboxMessage, product?: string) {
  const outcome = reply.forecast_at_confirmation?.find(f => f.product_id === product);
  if (!outcome) return "";
  if (outcome.dock_released === false) return "Waiting for the site to release a dock slot.";
  const change = outcome.change_from_previous;
  if (outcome.projected_packed == null || outcome.shortfall == null) return "Waiting for enough evidence for a packing projection.";
  const delta = change == null ? "" : ` (${change > 0 ? "+" : ""}${fmt(change)})`;
  const shared = outcome.joint_confirmations > 1 ? ` This projection includes ${outcome.joint_confirmations} confirmations together.` : "";
  return `${outcome.edition} forecast at confirmation: ${fmt(outcome.projected_packed)} by close${delta}, ${fmt(outcome.shortfall)} short.${shared}`;
}

function CaseUpdates({ snap, deliveries, selected, refresh, current, onAction }: {
  deliveries: ReturnType<typeof emailDeliveries>;
  snap: Snapshot; selected: number | null; refresh: () => Promise<void>; current: boolean; onAction: (key: string) => void;
}) {
  const [all, setAll] = useState(false);
  const queue = snap.queue ?? [];
  const decided = snap.decisions.filter(d => d.status === "rejected" || (d.chosen_option != null && ["approved", "executed", "outdated"].includes(d.status)))
    .sort((a, b) => (b.decided_at ?? "").localeCompare(a.decided_at ?? "") || b.id - a.id);
  if (!decided.length) return null;
  return (
    <section id="decision-follow-up" aria-labelledby="decided">
      <h2 id="decided" className="mb-3 text-xl font-bold">
        Decided
      </h2>
      <ul className="divide-y divide-rule rounded-lg border border-rule bg-surface">
        {(all ? decided : decided.filter((item, index) => index < 4 || item.id === selected)).map((d) => {
          const option = d.options[d.chosen_option!];
          const sent = snap.outbox.find((o) => o.decision_id === d.id);
          const reply = sent && snap.inbox.filter((i) => i.outbox_id === sent.id).sort((a, b) => b.id - a.id)[0];
          const review = snap.issues.find(i => i.type === "recovery_review" && i.evidence.some(e => e.path === `decisions/${d.id}`));
          const problem = snap.trace.find(log => log.action === "error" &&
            ((d.status === "approved" && !review && sent?.status !== "sent" && !reply && log.data?.decision_id === d.id)
              || (reply && !reply.processed && log.data?.inbox_id === reply.id)));
          const state = d.status === "rejected" ? "Rejected"
            : d.status === "outdated" ? "Outdated, see its history"
            : reply ? replyOutcome(reply, snap.setup?.timezone)
            : sent?.status === "failed" ? "Email failed, check it"
            : sent?.status === "draft" ? "Email draft ready"
            : sent?.status === "sent" ? (deliveries.get(sent.id) === "simulated" ? "Simulated email sent" :
              deliveries.get(sent.id) === "sent" ? "Email sent, waiting for reply" : "Email recorded, delivery unverified")
            : sent?.status === "escalated" ? "Escalated to the buyer"
            : review ? `Review with ${d.approver || "the buyer"}`
            : "Approved";
          const accepted = sent && snap.inbox.find(i => i.outbox_id === sent.id && i.result?.confirmed_delivery);
          const outcome = accepted ? forecastAfterReply(accepted, option?.product_id) : "";
          const pendingInquiry = queue.find(e => e.option.followup_for === d.id);
          const child = snap.decisions.find(c => c.chosen_option != null && c.options[c.chosen_option].followup_for === d.id);
          const lastInquiry = snap.decisions.find(c => c.options.some(o => o.followup_for === d.id));
          const inquiryMail = child && snap.outbox.find(o => o.decision_id === child.id);
          const correspondence = inquiryMail && snap.inbox.find(i => i.outbox_id === inquiryMail.id && i.result.qualification_correspondence);
          const priced = reply && reply.result?.supplier_quote ? queue.find(e => e.option.actions.some(a => a.quote_inbox_id === reply.id)) : undefined;
          return (
            <li key={d.id} id={`followup-${d.id}`} tabIndex={-1} className="scroll-mt-4 px-4 py-2.5 outline-none">
              <p className="flex items-baseline justify-between gap-3">
                <span className="min-w-0 truncate text-sm font-semibold" title={option?.label ?? d.title}>
                  {option?.label ?? d.title}
                </span>
                <span className={`shrink-0 text-xs font-semibold ${d.status === "rejected" ? "text-stop" : d.status === "outdated" ? "text-muted" : "text-ok"}`}>
                  {d.status === "rejected" ? "✕" : "✓"} {d.decided_by}
                </span>
              </p>
              <p className="mt-0.5 text-xs text-muted">
                {problem ? `${agentName(problem.agent)} reported an error on ${String(problem.data?.event ?? "this step")}` : state}
              </p>
              {outcome && <p className="mt-0.5 text-xs font-semibold">{outcome}</p>}
              {(priced || sent || problem) && (
                <p className="mt-1 flex flex-wrap gap-x-3 text-xs">
                  {priced && <button className="font-semibold text-act hover:underline" onClick={() => onAction(priced.key)}>Review priced offer</button>}
                  {sent && <Link className="text-act hover:underline" href={mailHref(sent.id)}>Supplier thread</Link>}
                  {problem && <a className="font-semibold text-warn hover:underline" target="_blank" rel="noreferrer" href={API_URL + "/api/trace?trace_id=" + problem.id}>Agent error trace (JSON)</a>}
                </p>
              )}
              {review && (
                <details className="mt-1 text-xs" data-assigned-review>
                  <summary className="cursor-pointer font-semibold text-act">Open assigned review #{review.id}</summary>
                  <p className="mt-1 font-semibold">{review.impact.note}</p>
                  <p>{review.reason}</p>
                  <p className="text-muted">Review status · {review.status}</p>
                  {pendingInquiry && <SampleInquiry entry={pendingInquiry} buyer={snap.setup?.demo_buyer} current={current} refresh={refresh} />}
                  {!pendingInquiry && !child && lastInquiry?.status === "outdated" && <p className="my-2 text-warn">
                    The earlier sample inquiry is outdated. Verify the current terms with the review owner.</p>}
                  {inquiryMail && <p className="my-2">{correspondence ? "Supplier response recorded for Engineering and Quality." :
                    inquiryMail.status === "failed" ? "The sample inquiry failed to send." : inquiryMail.status === "draft" ? "Sample inquiry drafted." :
                    "Sample inquiry recorded in the supplier thread."}
                    {" "}<Link className="text-act underline" href={mailHref(inquiryMail.id)}>Open sample correspondence</Link></p>}
                  <Link className="text-act underline" href={findingHref(review.id)}>View review record</Link>
                </details>
              )}
              <History decision={d.id} activity={snap.health?.activity_version} />
            </li>
          );
        })}
      </ul>
      {decided.length > 4 && <button className="mt-2 text-sm font-semibold text-act hover:underline" onClick={() => setAll(!all)}>
        {all ? "Show fewer" : `Show all ${decided.length}`}</button>}
    </section>
  );
}

function SampleInquiry({ entry, buyer, current, refresh }: {
  entry: BuyerQueueEntry; buyer?: string; current: boolean; refresh: () => Promise<void>;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const owners = entry.option.approvers || [];
  const send = async () => {
    setBusy(true);
    setError("");
    try {
      const result = await postData<ActionRun>(`/api/decisions/${entry.decision_id}/approve`, {
        by: buyer, option: entry.option_index, version: entry.version,
      });
      setError(runWarning(result));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      await refresh();
      setBusy(false);
    }
  };
  return <section className="my-3 max-w-2xl rounded border border-rule p-3">
    <h3 className="font-semibold">{entry.option.label}</h3>
    <p>{entry.next_step}</p>
    <details className="my-2"><summary className="cursor-pointer text-act">Preview the request</summary>
      <p className="mt-2">{String(entry.option.actions[0].ask)}</p></details>
    {owners.length === 1 && owners[0] === buyer
      ? <button className="rounded bg-act px-3 py-1.5 font-semibold text-white disabled:opacity-50" disabled={!current || busy} onClick={send}>
        {busy ? "Sending…" : "Send sample inquiry"}</button> : <p>Awaiting {owners.join(" + ") || "assignment"}</p>}
    {error && <p role="alert" className="text-stop">{error}</p>}
  </section>;
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
  title: string;
  updates?: React.ReactNode;
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
  // A replay shows a recorded snapshot: count down from the snapshot time, not from today's clock.
  const from = props.replayEnabled ? parseTs(props.snapshotTime)?.getTime() : props.now;
  const left = end && from ? end.getTime() - from : null;
  const closes = end?.toLocaleString("en-GB", {
    weekday: "short", hour: "2-digit", minute: "2-digit", timeZone: props.setup?.timezone || "UTC",
  });
  const snapshot = parseTs(props.snapshotTime)?.toLocaleString("en-GB", {
    weekday: "short", hour: "2-digit", minute: "2-digit", timeZone: props.setup?.timezone || "UTC",
  });
  const status = props.online ? "Backend online" : props.online === false ? "Backend offline" : "Connecting";
  return (
    <header className="flex flex-wrap items-center justify-between gap-x-6 gap-y-3 border-b border-rule py-5">
      <div>
        {props.setup && <p className="mb-0.5 text-xs font-medium text-muted">{[props.setup.customer, props.setup.site].filter(Boolean).join(" · ")}</p>}
        <h1 id="buyer-page-heading" tabIndex={-1} className="scroll-mt-14 text-[30px] font-bold leading-tight tracking-tight outline-none">{props.title}</h1>
        <p className="mt-1 flex flex-wrap gap-x-3 gap-y-0.5 text-xs text-muted">
          {props.setup?.demo_buyer && <span>Demo view · {props.setup.demo_buyer} · Procurement buyer</span>}
          {props.title === buyerViews.overview && closes && <span title={props.replayEnabled && snapshot ? `From the snapshot of ${snapshot}` : undefined}>
            Packing closes {closes}{left != null && left > 0 && ` · ${Math.floor(left / 3_600_000)}h ${String(Math.floor((left % 3_600_000) / 60_000)).padStart(2, "0")}m left`}
          </span>}
        </p>
      </div>
      <div className="flex flex-wrap items-center gap-2 sm:gap-3">
        {props.updates}
        {props.setup && (
          <ApprovalsChip autonomy={props.setup.autonomy} matrix={props.setup.approval_matrix} buyer={props.setup.demo_buyer} />
        )}
        {props.online != null && props.setup && <span
          title={props.mock ? "Historical or simulated inputs. Email delivery is simulated." : "Live data. Email delivery follows the Chaser configuration."}
          className={`rounded px-2 py-1 text-xs font-bold ${props.mock ? "bg-warn-soft text-warn" : "bg-ok-soft text-ok"}`}
        >
          {props.replayEnabled ? "Replay" : props.mock ? "Mock" : "Live"}
        </span>}
        <span
          role="img"
          aria-label={status}
          title={status}
          className={`inline-block h-2.5 w-2.5 rounded-sm ${
            props.online ? "bg-ok" : props.online === false ? "bg-stop" : "bg-rule"
          }`}
        />
        {props.replayEnabled && <button
          onClick={props.onDrop}
          disabled={props.busy || !props.online}
          className="rounded-md bg-ink px-4 py-2 text-sm font-semibold text-white hover:bg-act disabled:cursor-not-allowed disabled:opacity-50"
          title="Resumes the next recorded drop in the dedicated replay database."
        >
          {props.busy ? (
            "Agents are working"
          ) : props.retry ? (
            "Retry drop"
          ) : (
            <>
              <span className="sm:hidden">Next drop</span>
              <span className="hidden sm:inline">Play next data drop</span>
            </>
          )}
        </button>}

      </div>
    </header>
  );
}

const approvalCurrency = new Intl.NumberFormat("en-IE", {
  style: "currency", currency: "EUR", minimumFractionDigits: 0, maximumFractionDigits: 2,
});

const DecisionView = memo(function DecisionView({ entry, snap, current, onDone, onNotice, onDecided }: {
  entry: BuyerQueueEntry; snap: Snapshot; current: boolean; onDone: () => Promise<void>; onNotice: (message: string) => void;
  onDecided: (id: number) => void;
}) {
  const [busy, setBusy] = useState(false);
  const option = entry.option;
  const buyer = snap.setup?.demo_buyer;
  const approvalCurrency = formatMoney(option.currency || "EUR");
  const assigned = option.approvers ?? [];
  const pendingRoles = option.assignments?.filter(a => !a.person).map(a => a.required_role) ?? [];
  const ownsAction = !!buyer && !pendingRoles.length && assigned.length === 1 && assigned[0] === buyer;
  const cost = option.cost_eur;
  const value = option.approval_value_eur;
  const card = snap.decisions.find(d => d.id === entry.decision_id);
  const noticeIndex = card?.evidence.findIndex(e => e.locator === "delivery window") ?? -1;
  const comparison = entry.freight_comparison;
  const alternative = comparison?.alternatives[0];
  const tradeoff = comparison?.difference;
  const baseline = card?.options.find(o => o.description === "baseline");
  const action = option.actions[0];
  const button = option.button_label || (action?.type === "human_review" ? "Open review" :
    action?.quote_inbox_id ? "Authorize quote" : action?.type === "email" ? "Send supplier request" : "Approve");
  const signingRoles = option.assignments?.map(a => a.signing_role) ?? option.approval_roles ?? [];
  const role = [...new Set(signingRoles)].join(" + ") || "Unassigned role";
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
      const result = await postData<ActionRun>(`/api/decisions/${entry.decision_id}/${verb}`, {
        by: buyer, option: entry.option_index, version: entry.version,
      });
      onDecided(entry.decision_id);
      onNotice(runWarning(result) || (verb === "reject" ? "Decision rejected." : action?.type === "human_review"
        ? `Internal review authorized. See decision follow-up for the owner and record.`
        : "Supplier request authorized. See decision follow-up for delivery status."));
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
      <p className="text-sm text-muted">{entry.edition}{entry.part ? " · " + entry.part : option.horizon !== "future" ? " · Packing capacity" : ""}</p>
      <h3 className="mt-1 text-lg font-bold leading-snug">{option.label}</h3>
      {option.exposure ? <ExposureSummary exposure={option.exposure} /> :
        <p className="mt-2 text-sm">{entry.next_step}</p>}
      {!!option.shipment_checks?.length && <p className="mt-2 text-sm font-semibold text-warn">
        Shipment records disagree on {option.shipment_checks.map(c => c.field === "eta" ? "arrival date" : "location status").join(" and ")}.
      </p>}
      {action?.quote_inbox_id != null && <p className="mt-1 text-sm font-semibold text-act">Quote received · Awaiting authorization</p>}
      {option.launch_priority?.all_demand_preordered && !!option.copies_gained && <p className="mt-3 text-sm font-medium text-act">
        Customer priority · All {fmt(option.launch_priority.preordered)} {entry.edition} copies are preordered.
      </p>}
      <dl className="my-4 flex flex-wrap gap-x-7 gap-y-3 text-sm tabular">
        {option.horizon !== "future" && <>
          <div><dt className="text-muted">Conditional gain</dt><dd className="font-semibold">
            {option.copies_gained == null ? "Unknown" : `Up to ${fmt(option.copies_gained)} copies`}</dd>
            {!!option.copies_gained && option.remaining_shortfall != null && baseline?.remaining_shortfall != null &&
              <dd className="text-xs text-muted">{fmt(baseline.remaining_shortfall)} → {fmt(option.remaining_shortfall)} short</dd>}</div>
          <div><dt className="text-muted">{option.description === "supplier stock offer" ? "Quoted goods" : "Cost"}</dt><dd>{cost == null ? "Unknown" : approvalCurrency.format(cost)}</dd></div>
        </>}
        <div><dt className="text-muted">{option.horizon === "future" ? "Spend authorized" : "Approval value"}</dt><dd>{value == null ? "Unknown" : approvalCurrency.format(value)}</dd></div>
        <div><dt className="text-muted">Assigned to</dt><dd>{pendingRoles.length ? "Pending assignment" : assigned.join(" + ") || "Pending assignment"} · {role}</dd></div>
      </dl>
      {comparison && comparison.fee_per_additional_copy != null && <p className="mb-2 text-sm">
        {tradeoff && <strong>{tradeoff.fee_eur === 0 ? "Same fee" :
          `${approvalCurrency.format(Math.abs(tradeoff.fee_eur))} ${tradeoff.fee_eur < 0 ? "less" : "more"}`}
          {tradeoff.copies === 0 ? " for the same on-time gain" : ` for ${fmt(Math.abs(tradeoff.copies))} ${tradeoff.copies > 0 ? "more" : "fewer"} copies`}
          {" versus “"}{tradeoff.alternative}”. </strong>}
        The fee works out to {approvalCurrency.format(comparison.fee_per_additional_copy)} per additional copy if the delivery is confirmed on time.
        {alternative && !tradeoff && ` ${alternative.label} would cost ${approvalCurrency.format(alternative.fee)} for up to ${fmt(alternative.gain)} copies.`}
      </p>}
      {entry.shared_shipment && <p className="mb-2 text-sm text-muted">
        Other options use this shipment ({entry.shared_shipment}). Count its copy gain once.
      </p>}
      {option.horizon === "future" && option.exposure?.sample_window
        ? <p className="text-sm"><strong>First move</strong> · Check the incumbent’s {option.exposure.sample_window.sample_days}-day sample offer with Engineering and Quality.</p>
        : <p className="text-sm"><strong>Still needed</strong> · {entry.requirements.join(" · ")}</p>}
    </div>
    <div className="mt-4 flex flex-wrap items-center gap-3 border-t border-rule px-5 py-3 text-sm">
      {ownsAction ? <button disabled={!current || busy} onClick={() => decide("approve")}
        className="rounded-md bg-act px-4 py-2 font-semibold text-white hover:bg-ink disabled:opacity-50">{button}</button>
        : <strong>{pendingRoles.length ? `Awaiting named ${pendingRoles.join(" + ").toLowerCase()}` :
          assigned.length ? `Awaiting ${assigned.join(" + ")}` : "Awaiting assignment"}{!pendingRoles.length && assigned.length > 0 && assigned.join(" + ") !== role && ` · ${role}`}</strong>}
      {deadline && <span className="text-muted">Decide by {deadline} {snap.setup?.timezone || "UTC"}</span>}
    </div>
    {option.exposure?.sample_window && <div className="border-t border-rule px-5 pb-3">
      <SampleTiming key={entry.version} decision={entry.decision_id} option={entry.option_index} window={option.exposure.sample_window} />
    </div>}
    <details className="border-t border-rule px-5 py-3 text-sm">
      <summary className="cursor-pointer font-semibold text-act">Why? · {issueIds.length} linked {issueIds.length === 1 ? "finding" : "findings"}</summary>
      {option.exposure ? <>
        {card && noticeIndex >= 0 && <SourcePassage decision={card.id} index={noticeIndex}
          evidence={card.evidence[noticeIndex]} label="Read the supplier’s delivery notice" />}
        <ExposureEvidence exposure={option.exposure} />
      </> : <>
        <p className="mt-3">{option.risk_note}</p>
        {option.launch_priority && <section className="mt-3">
          <h4 className="font-bold">Why this edition comes first</h4>
          <blockquote className="mt-1 border-l-2 border-act pl-3">{option.launch_priority.evidence.quote}
            {option.launch_priority.rationale && <p className="mt-1">{option.launch_priority.rationale}</p>}</blockquote>
          <p className="mt-1 text-xs text-muted">{option.launch_priority.evidence.path.split("/").pop()}</p>
        </section>}
        {!!option.shipment_checks?.length && <section className="mt-3">
          <h4 className="font-bold">What the shipment records say</h4>
          {option.shipment_checks.map(check => <ul key={check.field} className="mt-1 divide-y divide-rule">
            {check.readings.map(r => <li key={r.source} className="py-2">
              <strong className="capitalize">{r.value.replaceAll("_", " ")}</strong>{r.note && ` · ${r.note}`}
              <span className="block break-words text-xs text-muted">{r.source.split("/").pop()?.split("#")[0]}</span>
            </li>)}</ul>)}
        </section>}
        {entry.tracking_signal && <section className="mt-3">
          <h4 className="font-bold">Why the Risk Rater flagged this shipment</h4>
          <p>{entry.tracking_signal.text}</p>
          {entry.tracking_signal.selected_for_rating && <p className="text-muted">
            This was the tracking input used in the supplier’s {entry.tracking_signal.supplier_rating} rating.</p>}
          <p className="mt-1 break-words text-muted">{entry.tracking_signal.same_shipment_source &&
            "The signal uses one of the two records above. "}Source · {entry.tracking_signal.evidence.path.split("/").pop()}
            {entry.tracking_signal.observed_at && ` · Updated ${entry.tracking_signal.observed_at}`}</p>
        </section>}
        {option.description === "supplier stock offer" && <p className="mt-2">Opening this review commits no spend. A new PO needs separate approval.</p>}
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
      {option.horizon === "future" && <p className="mt-2">Opening this review assigns the work to {assigned.join(" + ") || role}. Future sourcing cost is {cost == null ? "unknown" : approvalCurrency.format(cost)}. Any purchase requires a separate decision.</p>}
      {option.horizon !== "future" && rule && value != null && <p className="mt-2">
        {approvalCurrency.format(value)} falls in the {rule.approvers.join(" + ")} approval band
        {" "}from {approvalCurrency.format(lower)}{rule.below_eur == null ? " upwards" : ` to below ${approvalCurrency.format(rule.below_eur)}`}.
        {option.actions.some(a => a.quote_inbox_id != null) && " This includes the original PO and the extra quoted charge."}
      </p>}
      {option.approval_basis && <OrderCalculation basis={option.approval_basis} />}
      {!!option.assignments?.length && <div className="mt-2">
        {option.assignments.map(a => <p key={a.required_role}>{a.person || a.required_role} · {a.basis}
          {a.org_source && <span className="text-muted"> {a.org_source.split("/").pop()}</span>}</p>)}
      </div>}
      <History decision={entry.decision_id} option={entry.option_index} activity={snap.health?.activity_version} />
      <details className="my-3"><summary className="cursor-pointer">Linked decisions and findings</summary><ul className="mt-2 space-y-2">{entry.sources.map(source => {
        const card = snap.decisions.find(d => d.id === source.decision_id);
        return <li key={`${source.decision_id}-${source.option_index}`}>
          <a className="text-act underline" target="_blank" rel="noreferrer" href={`${API_URL}/api/decisions/${source.decision_id}`}>Decision #{source.decision_id} (JSON)</a>
          {" · "}{card?.options[source.option_index]?.label ?? `Option ${source.option_index + 1}`}
          {source.related_review && " · Related internal review"}
          {source.issue_ids.map(id => <div key={id}><Link className="text-act underline" href={findingHref(id)}>Finding #{id}</Link>
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
      <SourceList evidence={entry.evidence} cards={snap.decisions.filter(c => entry.sources.some(s => s.decision_id === c.id))} />
      {ownsAction && snap.decisions.find(d => d.id === entry.decision_id)?.recommended === entry.option_index &&
        <button className="mt-3 font-semibold text-act" disabled={!current || busy} onClick={() => decide("reject")}>
          Reject source decision #{entry.decision_id}</button>}
    </details>
  </article>;
});

function CardDetails({ setup, card, similar, onDone }: { setup: CustomerSetup | null; card: DecisionCard; similar: DecisionCard[]; onDone: () => void }) {
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
  registered, online, traceReady, trace,
  now,
  busy,
  since,
  onOpenData,
}: {
  registered?: string[]; online: boolean | null; traceReady: boolean;
  trace: BuyerActivity[];
  now: number;
  /** A data drop is running right now. */
  busy: boolean;
  /** When it started (ms), to tell which agents already picked it up. */
  since: number | null;
  onOpenData: () => void;
}) {
  const stats = useMemo(() => {
    const out = new Map<string, { last?: BuyerActivity; count: number }>();
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
          <span>Agent Team</span>
          {busy && <span className="text-sm font-semibold text-act">working on the drop</span>}
        </h2>
        <button
          onClick={onOpenData}
          className="text-sm font-semibold text-act hover:underline"
          title="Open the source records used by the agents"
        >
          Source records
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
          const unavailable = a.id !== "human" && registered != null && !registered.includes(a.id);
          const state = online !== true ? (online === false ? "Backend offline" : "Connecting")
            : unavailable ? "Not registered" : !traceReady ? "Latest activity unavailable" : null;
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
                  className={`inline-block h-2 w-2 shrink-0 rounded-sm transition-opacity ${!state && active ? "beat" : ""}`}
                  style={{ background: a.color, opacity: waiting ? 0.35 : s ? 1 : 0.3 }}
                />
                <span className="min-w-0 flex-1">
                  <span className="block text-sm font-semibold">{a.name}</span>
                  <span className="block truncate text-xs text-muted" title={s?.last ? entryDetail(s.last) : undefined}>
                    {/* An error shows only while it is the agent's newest step; one old hiccup does not pin the lane. */}
                    {state ?? (s?.last?.action === "error" ? (
                      <span className="font-semibold text-stop">Error</span>
                    ) : waiting ? (
                      "Waiting"
                    ) : s?.last ? (
                      sentenceCase(actionLine(s.last))
                    ) : (
                      a.id === "human" ? "Decision history" : "Ready · no recent activity"
                    ))}
                  </span>
                </span>
                <span className="shrink-0 text-xs text-muted tabular">{!state && s ? ago(s.last?.ts, now) : ""}</span>
              </Link>
            </li>
          );
        })}
      </ul>
      </div>
    </section>
  );
}

type LogRow = { key: string; e: BuyerActivity; count: number; details: string[]; oldest: number };

/** One agent doing the same thing within one run becomes one row with a count: a drop re-checks every finding, and the
 *  Planner words each card with an AI call between the cards. Entries without a run id fold only with their neighbour.
 *  The trace is newest first, so a row shows its newest entry; its key (run + agent + action, or the oldest entry id)
 *  stays the same while newer entries of that run arrive, so an opened "+N more" list stays open. */
function foldLog(trace: BuyerActivity[], max = 40): LogRow[] {
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

function LiveLog({ trace, limit = 40 }: { trace: BuyerActivity[]; limit?: number }) {
  const rows = useMemo(() => foldLog(trace, limit), [trace, limit]);
  return (
    <section aria-label="What the agents just did">
      <ol className="rounded-lg border border-rule bg-surface text-sm">
        {rows.length === 0 && <li className="px-4 py-6 text-muted">The agents have not done anything yet.</li>}
        {rows.map(({ key, e, count, details }) => (
          <li key={key} className="flex gap-3 border-b border-rule px-4 py-2.5 last:border-b-0">
            <span className="w-16 shrink-0 pt-0.5 text-xs text-muted tabular">{clock(e.ts)}</span>
            <span className="min-w-0 flex-1">
              <span className="flex items-center gap-2">
                <span aria-hidden className="h-2 w-2 shrink-0 rounded-sm" style={{ background: agentColor(e.agent) }} />
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

function FindingDetail({ id, issue, queue, cards }: {
  id: number | null; issue?: Issue; queue: BuyerQueueEntry[]; cards: DecisionCard[];
}) {
  const [record, setRecord] = useState<Issue>();
  const [error, setError] = useState("");
  const [attempt, setAttempt] = useState(0);
  // The list is bounded. An older citation can still be opened by its permanent finding ID.
  const listed = issue != null;
  useEffect(() => {
    if (id == null || listed) return;
    let active = true;
    getData<Issue>(`/api/issues/${id}`).then(value => { if (active) setRecord(value); })
      .catch(err => { if (active) setError(err instanceof ApiHttpError && err.status === 404
        ? "This finding is no longer available." : "Could not load this finding."); });
    return () => { active = false; };
  }, [id, listed, attempt]);
  const finding = issue ?? record;
  const recordAvailable = finding != null;
  useEffect(() => {
    if (recordAvailable) document.getElementById("buyer-record-heading")?.focus({preventScroll:true});
  }, [recordAvailable, id]);
  const actions = finding ? queue.filter(entry => entry.sources.some(source => source.issue_ids.includes(finding.id))) : [];
  return <div className="max-w-4xl space-y-4">
    <Link href="/?view=findings" className="text-sm font-semibold text-act hover:underline">← All findings</Link>
    {finding ? <article className="space-y-4 rounded-lg border border-rule bg-surface p-5">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted">
        <SeverityLabel severity={finding.severity} /><span>{sentenceCase(finding.status)}</span>
        <span>{agentName(finding.raised_by)} · Finding #{finding.id}</span>
      </div>
      <h2 id="buyer-record-heading" tabIndex={-1} className="text-xl font-semibold leading-snug outline-none">{finding.summary || `Finding #${finding.id}`}</h2>
      {finding.reason && finding.reason !== finding.summary && <p className="break-words text-sm">{finding.reason}</p>}
      {finding.impact?.note && finding.impact.note !== finding.reason && <p className="text-sm">{finding.impact.note}</p>}
      {actions.length > 0 ? <div className="border-t border-rule pt-3 text-sm">
        <h3 className="font-semibold">Buyer actions</h3>
        <ul className="mt-1 space-y-1">{actions.map(entry => <li key={entry.key}>
          <Link className="text-act underline" href={actionHref(entry.key)}>{entry.option.label}</Link>
        </li>)}</ul>
      </div> : finding.suggested_actions.length > 0 && <div className="text-sm">
        <h3 className="font-semibold">Suggested actions</h3>
        <ul className="mt-1 list-disc pl-5">{finding.suggested_actions.map((action, index) => <li key={index}>{action.label}</li>)}</ul>
      </div>}
      {!!finding.evidence.length && <SourceList evidence={finding.evidence} cards={cards} />}
    </article> : <p role="status" className="text-sm text-muted">
      {id == null ? "This finding link is invalid." : error || "Loading finding…"}
      {error === "Could not load this finding." && <button className="ml-2 font-semibold text-act underline" onClick={() => {setError(""); setAttempt(n => n+1);}}>Retry</button>}
    </p>}
  </div>;
}

function Findings({ issues }: { issues: Issue[] }) {
  const rows = [...issues].sort(
    (a, b) => Number(a.status === "resolved") - Number(b.status === "resolved") || b.severity - a.severity,
  );
  if (rows.length === 0) return <p className="text-muted">No findings yet.</p>;
  return (
    <div className="overflow-x-auto rounded-lg border border-rule bg-surface">
      <table aria-label="Findings by severity" className="w-full text-left text-sm">
        <thead className="sr-only border-b border-rule text-muted sm:not-sr-only">
          <tr>
            <th className="hidden px-4 py-2 font-semibold sm:table-cell">Severity</th>
            <th className="px-4 py-2 font-semibold">Finding</th>
            <th className="hidden px-4 py-2 font-semibold sm:table-cell">Agent</th>
            <th className="hidden px-4 py-2 font-semibold sm:table-cell">Status</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(i => (
              <tr key={i.id} className={`border-b border-rule last:border-b-0 ${i.status === "resolved" ? "opacity-50" : ""}`}>
                <td className="hidden px-4 py-2 align-top sm:table-cell">
                  <SeverityLabel severity={i.severity} className="pt-0.5" />
                </td>
                <td className="align-top">
                  <Link href={findingHref(i.id)} className="block break-words px-4 py-3 text-act hover:bg-act-soft sm:py-2">
                    <span className="mb-1.5 flex flex-wrap items-center gap-2 text-xs text-muted sm:hidden">
                      <SeverityLabel severity={i.severity} />{sentenceCase(i.status)}
                    </span>
                    <span className="font-medium">{i.summary || `Finding #${i.id}`}</span>
                    <span className="mt-1 block text-xs text-muted sm:hidden">{agentName(i.raised_by)}</span>
                  </Link>
                </td>
                <td className="hidden px-4 py-2 align-top sm:table-cell">{agentName(i.raised_by)}</td>
                <td className="hidden px-4 py-2 align-top sm:table-cell">{sentenceCase(i.status)}</td>
              </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Emails({ activeMail, snap, mock, canRecord, onSent, deliveries }: { deliveries: ReturnType<typeof emailDeliveries>; activeMail: number | null; snap: Snapshot; mock: boolean; canRecord: boolean; onSent: () => void }) {
  const {outbox, inbox} = snap;
  const sampleDecisions = new Set(snap.decisions.filter(d => d.chosen_option != null &&
    d.options[d.chosen_option]?.actions.some(a => a.purpose === "verify_sample_terms")).map(d => d.id));
  const outboxIds = new Set(outbox.map(m => m.id));
  const unlinked = inbox.filter(m => m.outbox_id == null || !outboxIds.has(m.outbox_id));
  if (outbox.length === 0 && !unlinked.length) return <p className="text-muted">No supplier emails yet. The Chaser writes one when a finding needs an answer.</p>;
  return (
    <ul className="space-y-3">
      {(activeMail == null ? outbox : outbox.filter(m => m.id === activeMail)).map((m) => (
        <li id={`mail-${m.id}`} key={m.id}>
          <EmailView open={activeMail === m.id} delivery={deliveries.get(m.id)} m={m} canRecord={canRecord} replies={inbox.filter((r) => r.outbox_id === m.id)} mock={mock} onSent={onSent}
            timezone={snap.setup?.timezone} samples={m.decision_id != null && sampleDecisions.has(m.decision_id)} />
        </li>
      ))}
      {activeMail == null && unlinked.map(reply => <li key={`inbox-${reply.id}`}>
        <details className="rounded-lg border border-rule bg-surface px-5 py-3 text-sm">
          <summary className="cursor-pointer font-semibold">{reply.subject || "Supplier reply"} · {reply.supplier_id || reply.from_email}</summary>
          <p className="mt-2 text-xs text-muted">Incoming reply · No matching outgoing thread in this view</p>
          <p className="my-2 whitespace-pre-line">{reply.body_en || reply.body}</p>
          <p className="text-muted">{replyOutcome(reply, snap.setup?.timezone)}</p>
        </details>
      </li>)}
    </ul>
  );
}

function EmailView({ open, m, canRecord, delivery, replies, mock, onSent, timezone, samples }: { delivery?: "sent" | "simulated" | "unverified"; open: boolean; m: OutboxMessage; canRecord: boolean; replies: InboxMessage[]; mock: boolean; onSent: () => void; timezone?: string; samples: boolean }) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    if (open) document.getElementById("buyer-record-heading")?.focus({preventScroll:true});
  }, [m.id, open]);
  const deliveryLabel = delivery === "unverified" ? "delivery unverified" : delivery;
  const status = m.status === "sent" ? (deliveryLabel ?? m.status) :
    m.status === "replied" && deliveryLabel && deliveryLabel !== "sent" ? `replied · ${deliveryLabel}` : m.status;

  const reply = async () => {
    setBusy(true);
    setError("");
    try {
      const result = await postData<ActionRun>("/api/inbox", {
        supplier_id: m.supplier_id, body: text, issue_id: m.issue_id ?? undefined, outbox_id: m.id,
        subject: `Re: ${m.subject}`,
      });
      setText("");
      setError(runWarning(result));
      onSent();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <article className="rounded-lg border border-rule bg-surface">
      <div className="flex w-full flex-wrap items-baseline gap-x-3 px-5 py-3 text-left">
        {open ? <h2 id="buyer-record-heading" tabIndex={-1} className="font-semibold outline-none">{m.subject || "Supplier request"}</h2> :
          <Link className="font-semibold text-act hover:underline" href={mailHref(m.id)}>{m.subject || "Supplier request"}</Link>}
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
      </div>
      {open && (
        <div className="border-t border-rule px-5 py-4 text-sm">
          {m.issue_id != null && <Link className="mb-3 inline-block font-medium text-act underline" href={findingHref(m.issue_id)}>Finding #{m.issue_id}</Link>}
          <p className="whitespace-pre-line">{m.body_en || m.body}</p>
          {replies.map((r) => (
            <div key={r.id} className="mt-4 border-l-2 border-ok pl-3">
              <p className="text-xs text-muted">Reply from {r.supplier_id}, {clock(r.received_at, timezone || "UTC")} {timezone || "UTC"}</p>
              <p className="whitespace-pre-line">{r.body}</p>
              <p className="mt-1 text-xs text-muted">{replyOutcome(r, timezone)}</p>
            </div>
          ))}
          <label className="mt-4 block text-xs font-semibold text-muted" htmlFor={`reply-${m.id}`}>
            {mock ? "Test: answer as the supplier" : "Record a reply received from the supplier"}
          </label>
          <textarea
            id={`reply-${m.id}`}
            value={text}
            onChange={(e) => setText(e.target.value)}
            rows={2}
            className="mt-1 w-full rounded-md border border-rule p-2"
            placeholder={samples ? "Give sample availability, specifications, freight and qualification terms." : "Confirm the order line, revision, quantity and delivery time."}
          />
          <button
            onClick={reply}
            disabled={!canRecord || busy || !text.trim()}
            className="mt-2 rounded-md bg-ink px-3 py-1.5 text-sm font-semibold text-white hover:bg-act disabled:opacity-50"
          >
            {mock ? "Send test reply" : "Record supplier reply"}
          </button>
          {error && <p className="mt-2 text-stop">{error}</p>}
        </div>
      )}
    </article>
  );
}

/** What Ingest did with a supplier reply, in one sentence. */
function replyOutcome(r: InboxMessage, timezone = "UTC"): string {
  const res = r.result ?? {};
  if (!r.processed) return "Waiting for Ingest.";
  if (typeof res.review === "string" && res.review.startsWith("Duplicate of inbox/"))
    return "Duplicate reply. The original evidence and forecast still apply.";
  if (typeof res.review === "string") return `Held for review: ${res.review}`;
  if (res.qualification_correspondence) return `Ingest attached this response to review #${res.qualification_correspondence.review_id}. Engineering and Quality assessment is pending.`;
  const advice = (res.confirmed_delivery ?? res.supplier_quote) as Record<string, unknown> | undefined;
  if (advice) {
    const time = (value: unknown) => parseTs(String(value))?.toLocaleString("en-GB", {
      weekday: "short", day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", timeZone: timezone,
    });
    return res.supplier_quote
      ? `Supplier quoted ${formatMoney().format(Number(advice.incremental_cost_eur))} for ${String(advice.mode)} of ${fmt(Number(advice.quantity))} units. Offer expires ${time(advice.valid_until)} ${timezone}.`
      : `Ingest confirmed ${fmt(Number(advice.quantity))} units, revision ${String(advice.revision)}, due ${time(advice.arrival)} ${timezone}. The Planner checks dock release and packing time.`;
  }
  const fields = Array.isArray(res.fields) ? (res.fields as string[]) : [];
  return fields.length
    ? `Ingest filled ${fields.join(" and ")} on ${String(res.rows)} order line(s).`
    : "Read by Ingest; nothing to fill.";
}

function MarketWatch({scores}: {scores: RiskScore[]}) {
  type Source = NonNullable<RiskScore["reasons"][number]["signal_inputs"]>[string][number];
  const watches = new Map<string, {source: Source; suppliers: Set<string>}>();
  for (const row of scores) {
    if (row.entity_type !== "supplier") continue;
    for (const source of row.reasons[0]?.signal_inputs?.external_politics ?? []) {
      if (source.provider !== "polymarket" || !source.market) continue;
      const key = JSON.stringify([source.market.question_id || source.title, source.market.yes_pct, source.live, source.observed_at, source.last_known_at]);
      if (!watches.has(key)) watches.set(key, {source, suppliers:new Set()});
      watches.get(key)!.suppliers.add(row.entity_id + " · " + row.rating);
    }
  }
  if (!watches.size) return null;
  return <details className="basis-full border-t border-rule pt-2 text-sm" open={watches.size === 1 || undefined}>
    <summary className="cursor-pointer font-semibold text-act">Polymarket questions in supplier ratings ({watches.size})</summary>
    <ul className="mt-2 divide-y divide-rule">{[...watches].map(([key, {source, suppliers}]) => <li key={key} className="py-2">
      <p><strong className="mr-2 tabular">{source.market!.yes_pct}% Yes</strong>{" "}
        {source.live && /^https?:\/\//i.test(source.evidence.path)
          ? <a className="text-act underline" href={source.evidence.path} target="_blank" rel="noreferrer">{source.title} ↗</a> : source.title}</p>
      <p className="text-xs text-muted">{source.live ? "Public market at collection" : "Offline example"} · {source.market!.scope_basis || source.scope}
        {source.market!.disruption_outcome === "No" && " · The No side measures disruption"} · {Array.from(suppliers).sort().join(", ")}</p>
      {source.last_known_at && <p className="text-xs text-warn">Last successful reading {source.last_known_at.slice(0,16).replace("T"," ")} UTC.</p>}
    </li>)}</ul>
  </details>;
}

function SignalRefresh({reading, scores, online, mock, refresh, timezone = "UTC"}: {
  reading: HealthCheckResponse["lastRiskCheck"]; scores: RiskScore[]; online: boolean; mock: boolean; refresh: () => Promise<void>; timezone?: string;
}) {
  const failed = Object.keys(reading?.unavailable ?? {});
  return <div className="mb-3 flex flex-wrap items-center justify-between gap-3 rounded-lg border border-rule bg-surface p-4">
    <div className="text-sm"><h3 className="font-bold">External signal check</h3>
      <p>{reading ? <>{mock ? "Offline evidence" : "Live monitoring"} · Checked {clock(reading.at, timezone)} {timezone} · {reading.grades_changed == null
        ? "previous grades retained" : `${reading.grades_changed} grade ${reading.grades_changed === 1 ? "change" : "changes"}`}</> : "Waiting for the first check."}
        {reading && <> · <a className="text-act underline" target="_blank" rel="noreferrer" href={API_URL + "/api/trace?run_id=" + reading.run_id}>Check trace (JSON)</a></>}</p>
      {failed.length > 0 && <p role="alert" className="text-warn">Unavailable feeds · {failed.join(", ")}</p>}
    </div>
    <CheckSignals online={online} mock={mock} refresh={refresh} />
    <MarketWatch scores={scores} />
  </div>;
}
