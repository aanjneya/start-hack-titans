"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  EMPTY_OBSERVABILITY, loadObservabilitySnapshot, postData,
  type ObservabilitySnapshot,
} from "@/lib/api";
import { AGENT_NAV, agentMeta, reportHref, type AgentId, type AgentMeta } from "./agent-meta";
import {
  AgentTimeline, ChaserReport, HumanAuditReport, IngestReport, OverviewReport,
  PlannerReport, ReconcilerReport, RiskRaterReport, WatcherReport,
} from "./agent-reports";
import { AgentWorkspace } from "./workspace/agent-workspace";
import CheckSignals from "../check-signals";
import type { RunResult } from "../../../API_CONTRACT";

const VALID_IDS = new Set<string>(AGENT_NAV.map((a) => a.id));
const RISK_TOPICS = [["risk-ratings", "Ratings"], ["risk-signals", "Market signals"], ["risk-action", "Buyer action"], ["risk-scenarios", "Scenarios"]] as const;

export default function ObservabilityDashboard() {
  const searchParams = useSearchParams();
  const rawAgent = searchParams.get("agent") ?? "overview";
  const selectedId: AgentId = VALID_IDS.has(rawAgent) ? (rawAgent as AgentId) : "overview";

  const [snap, setSnap] = useState<ObservabilitySnapshot>(EMPTY_OBSERVABILITY);
  const [online, setOnline] = useState<boolean | null>(null);
  const [now, setNow] = useState<number>(0);
  const [dropBusy, setDropBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [riskUnavailable, setRiskUnavailable] = useState(false);
  const last = useRef(EMPTY_OBSERVABILITY);
  const nextFullRead = useRef(0);
  const reading = useRef(false);
  const refresh = useCallback(async () => {
    if (reading.current) return;
    reading.current = true;
    try {
      const previous = last.current;
      const allowUnchanged = Date.now() < nextFullRead.current;
      const { snap: next, online: up, missing } = await loadObservabilitySnapshot(previous, allowUnchanged);
      setOnline(up);
      if (up) {
        last.current = next;
        setSnap(next);
        setRiskUnavailable(missing.includes("risk"));
        if (missing.length) nextFullRead.current = 0;
        else if (!allowUnchanged || next.health?.activity_version !== previous.health?.activity_version) {
          nextFullRead.current = Date.now() + 30_000;
        }
      } else nextFullRead.current = 0;
      setNow(Date.now());
    } finally {
      reading.current = false;
    }
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
    setDropBusy(true);
    setNotice("");
    try {
      const r = await postData<RunResult & { done?: boolean; drops?: number; step: number; of: number }>("/api/replay/step");
      setNotice(
        r.done
          ? `All ${r.drops} recorded drops are in.`
          : r.status === "partial"
          ? `Drop ${r.step} needs review. ${r.failures.map(f => `${f.agent} failed on ${f.event} (trace #${f.trace_id})`).join(", ")}. Retry this drop after review.`
          : `Drop ${r.step} of ${r.of} processed. ${r.open_issues} findings are open.`,
      );
    } catch (e) {
      setNotice(`Could not process drop: ${(e as Error).message}`);
    } finally {
      setDropBusy(false);
      await refresh();
    }
  };

  const agentErrors = useMemo(() => {
    const counts = new Map<string, number>();
    for (const entry of snap.trace) {
      if (entry.action === "error") counts.set(entry.agent, (counts.get(entry.agent) ?? 0) + 1);
    }
    return counts;
  }, [snap.trace]);

  const activeMeta = agentMeta(selectedId);
  const mock = snap.health?.mockMode ?? true;
  const riskView = selectedId === "risk";
  const hasRatings = snap.risk.length > 0;
  const riskEntity = searchParams.get("entity");

  useEffect(() => {
    if (!riskView || !hasRatings) return;
    // A shared link may load before the data-backed section exists.
    const id = window.location.hash.slice(1);
    if (id === "risk-rating-detail" || RISK_TOPICS.some(([topic]) => topic === id)) {
      document.getElementById(id)?.scrollIntoView({ block: "start" });
    }
  }, [riskView, hasRatings, riskEntity]);

  return (
    <div className={`mx-auto flex min-h-[calc(100dvh-var(--workspace-nav-height,0px))] text-ink ${riskView ? "max-w-[1560px]" : "max-w-[1720px]"}`}>
      <aside className="sticky top-[var(--workspace-nav-height)] max-h-[calc(100dvh-var(--workspace-nav-height))] w-[62px] shrink-0 self-start overflow-y-auto overscroll-contain border-r border-rule bg-surface px-0.5 py-2 min-[390px]:w-[66px] md:w-40 md:p-2 lg:w-48 lg:p-3">
        <ReportNavigation active={selectedId} errors={agentErrors} />
      </aside>

      <main className={`min-w-0 flex-1 wrap-anywhere px-2.5 pb-16 min-[390px]:px-3 sm:px-5 lg:px-7 ${riskView ? "risk-workspace" : ""}`}>
        <header className={riskView
          ? "flex flex-wrap items-center gap-x-4 gap-y-0 border-b border-rule py-2"
          : "flex flex-wrap items-end justify-between gap-3 border-b border-rule py-5"}>
          <div className="min-w-0">
            {!riskView && selectedId !== "overview" && <p className="mb-1 text-xs font-semibold text-act">{activeMeta.name}</p>}
            <h1 className="text-3xl font-bold leading-tight tracking-tight">{activeMeta.report}</h1>
            {!riskView && <p className="mt-1 max-w-2xl text-sm text-muted">{activeMeta.role}</p>}
          </div>
          {riskView && <nav aria-label="Risk report sections"
            className="risk-topic-nav order-last w-full border-b-0! lg:order-none lg:ml-auto lg:w-auto">
            {RISK_TOPICS.map(([id, title]) => <a key={id} href={`#${id}`}>{title}</a>)}
          </nav>}
          <div className={`flex flex-wrap items-center gap-3 text-xs ${riskView ? "ml-auto lg:ml-2" : ""}`}>
            {selectedId === "chaser" && <Link className="font-semibold text-act underline underline-offset-2" href="/?view=emails">Open supplier email</Link>}
            {selectedId === "reconciler" && <Link className="font-semibold text-act underline underline-offset-2" href="/?view=findings">Open findings</Link>}
            {snap.health && <span className="text-muted">{snap.health.replayEnabled ? "Replay data" : mock ? "Offline mode" : "Live data"}</span>}
            {riskView && <CheckSignals online={online === true} mock={mock} refresh={refresh} />}
            {selectedId === "overview" && snap.health?.replayEnabled && <button
              type="button"
              onClick={nextDrop}
              disabled={dropBusy || !online}
              className="rounded-lg bg-primary px-4 py-2 font-semibold text-primary-foreground hover:bg-brand-ink disabled:opacity-50"
            >
              {dropBusy ? "Agents working…" : snap.health.lastDrop?.status === "partial" ? "Retry drop" : "Play next data drop"}
            </button>}
          </div>
        </header>

        <div className={`space-y-6 ${riskView ? "mt-4" : "mt-5"}`}>
          {notice && (
            <p role="status" className="rounded-lg border border-rule bg-surface px-4 py-2.5 text-sm">
              {notice}
            </p>
          )}
          {online === false && (
            <p role="alert" className="rounded-lg bg-stop-soft px-4 py-3 text-sm text-stop">
              The connection is offline. Report updates are paused.
            </p>
          )}

          {selectedId === "overview" && (
            <OverviewReport snap={snap} now={now} online={online} />
          )}
          {selectedId === "watcher" && (
            <>
              <AgentWorkspace agent="watcher" snap={snap} />
              <WatcherReport snap={snap} />
            </>
          )}
          {selectedId === "ingest" && (
            <>
              <AgentWorkspace agent="ingest" snap={snap} />
              <IngestReport snap={snap} />
            </>
          )}
          {selectedId === "reconciler" && <ReconcilerReport snap={snap} />}
          {selectedId === "chaser" && <ChaserReport snap={snap} />}
          {selectedId === "planner" && <PlannerReport snap={snap} now={now} />}
          {riskView && <>
            {riskUnavailable && <p role="alert" className="rounded-lg bg-warn-soft px-4 py-3 text-sm text-warn">
              {snap.risk.length ? "The latest ratings could not be loaded. Showing the last available readings." : "Risk ratings are temporarily unavailable."}
              {" "}<button className="font-semibold underline" onClick={() => void refresh()}>Retry</button>
            </p>}
            {(snap.risk.length > 0 || (online && !riskUnavailable)) ? <RiskRaterReport snap={snap} />
              : online === null && <p role="status" className="py-3 text-sm text-muted">Loading risk ratings and their sources…</p>}
          </>}
          {selectedId === "human" && <HumanAuditReport snap={snap} />}

          <AgentTimeline trace={snap.trace} agentId={selectedId} now={now} />
        </div>
      </main>
    </div>
  );
}


const agentLinks = [agentMeta("risk"), ...AGENT_NAV.filter(agent => !["overview", "human", "risk"].includes(agent.id))];

function ReportNavigation({ active, errors }: { active: AgentId; errors: Map<string, number> }) {
  function link(agent: AgentMeta, label: string, shortLabel = label) {
    const count = errors.get(agent.id) ?? 0;
    return <Link href={reportHref(agent.id)} aria-current={agent.id === active ? "page" : undefined}
      aria-label={`${label}${agent.id === "human" || agent.id === "overview" ? "" : " · " + agent.report}${count ? ` · ${count} recorded errors` : ""}`}
      className={`relative flex min-h-11 min-w-0 flex-col items-center justify-center gap-0.5 rounded-md px-0.5 py-1.5 text-center text-[10.5px] font-medium leading-tight focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-act md:flex-row md:justify-start md:gap-2.5 md:px-2.5 md:py-2 md:text-left md:text-sm ${agent.id === active ? "bg-act-soft font-semibold text-act" : "text-muted hover:bg-dock hover:text-ink"}`}>
      <span aria-hidden className="shrink-0 [&>svg]:size-[17px]">{agent.icon}</span>
      <span className="md:hidden">{shortLabel}</span>
      <span className="hidden min-w-0 md:block">{label}</span>
      {count > 0 && <span aria-hidden title={`${count} recorded errors`}
        className="absolute right-0 top-0.5 rounded-sm bg-stop-soft px-0.5 text-[10px] font-semibold text-stop md:static md:ml-auto">
        {count > 99 ? "99+" : count}
      </span>}
    </Link>;
  }

  return <nav aria-label="Agent Team reports">
    <div className="mb-1 border-b border-rule pb-1.5 md:mb-2 md:pb-2">
      {link(agentMeta("overview"), "Agent Team", "Team")}
    </div>
    <ul aria-label="Agents" className="space-y-0.5">
      {agentLinks.map(agent => <li key={agent.id}>{link(agent,
        agent.id === "risk" ? "Risk Rater" : agent.shortName)}</li>)}
    </ul>
    <div className="mt-1 border-t border-rule pt-1.5 md:mt-2 md:pt-2">
      {link(agentMeta("human"), "Approval history", "Approvals")}
    </div>
  </nav>;
}
