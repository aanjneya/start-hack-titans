"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  API_URL, EMPTY_OBSERVABILITY, ago, fmt, loadObservabilitySnapshot, parseTs, postData,
  type ObservabilitySnapshot,
} from "@/lib/api";
import { AGENT_NAV, agentMeta, isPlumbingEvent, type AgentId } from "./agent-meta";
import {
  AgentTimeline, ChaserReport, HumanAuditReport, IngestReport, OverviewReport,
  PlannerReport, ReconcilerReport, RiskRaterReport, WatcherReport,
} from "./agent-reports";
import { AgentWorkspace } from "./workspace/agent-workspace";

const VALID_IDS = new Set<string>(AGENT_NAV.map((a) => a.id));

export default function ObservabilityDashboard() {
  const searchParams = useSearchParams();
  const router = useRouter();
  const rawAgent = searchParams.get("agent") ?? "overview";
  const selectedId: AgentId = VALID_IDS.has(rawAgent) ? (rawAgent as AgentId) : "overview";

  const [snap, setSnap] = useState<ObservabilitySnapshot>(EMPTY_OBSERVABILITY);
  const [online, setOnline] = useState<boolean | null>(null);
  const [now, setNow] = useState<number>(0);
  const [dropBusy, setDropBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [collapsed, setCollapsed] = useState(false);

  const selectAgent = useCallback(
    (id: AgentId) => {
      const qs = id === "overview" ? "" : `?agent=${id}`;
      router.replace(`/observability${qs}`, { scroll: false });
    },
    [router],
  );

  const refresh = useCallback(async () => {
    const { snap: next, online: up } = await loadObservabilitySnapshot();
    setOnline(up);
    if (up) setSnap(next);
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
    setDropBusy(true);
    setNotice("");
    try {
      const r = await postData<Record<string, number | string | boolean>>("/api/replay/step");
      setNotice(
        r.done
          ? `All ${r.drops} recorded drops are in. Restart the backend to replay again.`
          : `Drop ${r.step} of ${r.of} processed: +${r.added} added, ~${r.changed} changed, -${r.removed} removed (${r.open_issues} findings open).`,
      );
    } catch (e) {
      setNotice(`Could not process drop: ${(e as Error).message}`);
    } finally {
      setDropBusy(false);
      await refresh();
    }
  };

  const sidebarStats = useMemo(() => {
    const map = new Map<string, { count: number; errors: number; lastTs?: string }>();
    let totalReal = 0;
    let totalErrors = 0;
    for (const e of snap.trace) {
      if (e.action === "error") totalErrors += 1;
      const cur = map.get(e.agent) ?? { count: 0, errors: 0 };
      if (!isPlumbingEvent(e)) {
        cur.count += 1;
        totalReal += 1;
        if (!cur.lastTs) cur.lastTs = e.ts;
      }
      if (e.action === "error") cur.errors += 1;
      map.set(e.agent, cur);
    }
    map.set("overview", { count: totalReal, errors: totalErrors, lastTs: snap.trace[0]?.ts });
    return map;
  }, [snap.trace]);

  const activeMeta = agentMeta(selectedId);

  return (
    <div className="flex min-h-screen text-ink">
      {/* Left Agent Icon Sidebar. Phones always get the icon rail, so the report keeps its width. */}
      <aside
        aria-label="Agent navigation"
        className={`sticky top-0 flex h-screen shrink-0 flex-col border-r border-rule bg-surface transition-[width] ${
          collapsed ? "w-[72px]" : "w-[72px] md:w-[236px]"
        }`}
      >
        <div className="hidden items-center justify-between gap-2 border-b border-rule px-3 py-4 md:flex">
          {!collapsed && (
            <div className="min-w-0">
              <p className="truncate text-sm font-bold leading-tight">AI agents</p>
              <p className="truncate text-[11px] text-muted">A team of 6 agents + you</p>
            </div>
          )}
          <button
            type="button"
            onClick={() => setCollapsed((c) => !c)}
            title={collapsed ? "Expand sidebar" : "Collapse sidebar"}
            aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
            className="mx-auto flex h-8 w-8 shrink-0 items-center justify-center rounded-md border border-rule text-muted hover:bg-dock hover:text-ink"
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
              {collapsed ? <path d="M9 18l6-6-6-6" /> : <path d="M15 18l-6-6 6-6" />}
            </svg>
          </button>
        </div>

        <nav className="flex-1 space-y-1 overflow-y-auto p-2">
          {AGENT_NAV.map((a) => {
            const st = sidebarStats.get(a.id);
            const active = selectedId === a.id;
            const ts = parseTs(st?.lastTs);
            const pulsing = ts && now ? now - ts.getTime() < 15_000 : false;
            const errs = st?.errors ?? 0;
            const errText = `${errs} error${errs === 1 ? "" : "s"}`;
            const statusId = `agent-status-${a.id}`;
            const statusText = st?.count ? `${fmt(st.count)} · ${ago(st.lastTs, now)}` : "idle";
            return (
              <button
                key={a.id}
                type="button"
                onClick={() => selectAgent(a.id)}
                title={`${a.name} — ${a.role}`}
                aria-label={`${a.shortName}${errs > 0 ? `, ${errText}` : ""}`}
                aria-describedby={statusId}
                aria-current={active ? "page" : undefined}
                className={`relative flex w-full items-center gap-3 rounded-lg px-2.5 py-2.5 text-left text-sm transition ${
                  active
                    ? "bg-act-soft font-bold text-ink ring-1 ring-act/30"
                    : "text-muted hover:bg-dock hover:text-ink"
                }`}
              >
                <span
                  className={`relative flex h-9 w-9 shrink-0 items-center justify-center rounded-lg transition-colors ${
                    active ? "bg-primary text-primary-foreground shadow-xs" : "bg-secondary text-ink"
                  }`}
                >
                  {a.icon}
                  {pulsing && (
                    <span
                      className="beat absolute -right-0.5 -top-0.5 h-2.5 w-2.5 rounded-full border-2 border-surface bg-ok"
                      title="Active recently"
                    />
                  )}
                  {errs > 0 && (
                    <span
                      className="absolute -bottom-1 -right-1 flex h-4 min-w-[16px] items-center justify-center rounded-full bg-stop px-1 text-[10px] font-bold text-white"
                      title={errText}
                    >
                      {errs}
                    </span>
                  )}
                </span>

                <span id={statusId} className="sr-only">
                  {pulsing ? `${statusText}, active now` : statusText}
                </span>
                {!collapsed && (
                  <span aria-hidden className="hidden min-w-0 flex-1 md:block">
                    <span className="block truncate">{a.shortName}</span>
                    <span className="block truncate text-[11px] font-normal text-muted tabular">{statusText}</span>
                  </span>
                )}
              </button>
            );
          })}
        </nav>

        <div className="border-t border-rule p-2">
          <Link
            href="/"
            title="Back to Control Room"
            className="flex items-center justify-center gap-2 rounded-lg border border-rule bg-dock/60 px-3 py-2 text-xs font-semibold text-ink hover:bg-dock"
          >
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
              <path d="M19 12H5" />
              <path d="M12 19l-7-7 7-7" />
            </svg>
            {!collapsed && <span className="hidden md:inline">Control Room</span>}
          </Link>
        </div>
      </aside>

      {/* Main Visual Report Canvas */}
      <main className="min-w-0 flex-1 px-4 pb-16 sm:px-8">
        <header className="flex flex-wrap items-center justify-between gap-4 border-b border-rule py-5">
          <div className="flex items-center gap-3">
            <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-primary text-primary-foreground">
              {activeMeta.icon}
            </span>
            <div className="min-w-0">
              <h1 className="text-2xl font-bold leading-tight">{activeMeta.name}</h1>
              <p className="text-sm text-muted">{activeMeta.role}</p>
            </div>
          </div>

          <div className="flex flex-wrap items-center gap-3">
            <span className="flex items-center gap-1.5 text-sm text-muted">
              <span
                aria-hidden
                className={`inline-block h-2 w-2 rounded-full ${
                  online ? "bg-ok" : online === false ? "bg-stop" : "bg-rule"
                }`}
              />
              {online ? "Backend online" : online === false ? "Backend offline" : "Connecting"}
            </span>
            <button
              type="button"
              onClick={nextDrop}
              disabled={dropBusy || !online}
              className="rounded-lg bg-primary px-4 py-2 text-sm font-semibold text-primary-foreground transition-colors hover:bg-brand-ink disabled:cursor-not-allowed disabled:opacity-50"
            >
              {dropBusy ? "Agents working…" : "Play next data drop"}
            </button>
          </div>
        </header>

        <div className="mt-6 space-y-6">
          {notice && (
            <p role="status" className="rounded-lg border border-rule bg-surface px-4 py-2.5 text-sm">
              {notice}
            </p>
          )}
          {online === false && (
            <p role="alert" className="rounded-lg bg-stop-soft px-4 py-3 text-sm text-stop">
              No answer from the backend at {API_URL}. Start it from the repo root with{" "}
              <code className="font-semibold">backend/.venv/bin/uvicorn backend.app.main:app --port 8000</code>.
            </p>
          )}

          {selectedId === "overview" && (
            <OverviewReport snap={snap} now={now} online={online} onSelectAgent={selectAgent} />
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
          {selectedId === "planner" && <PlannerReport snap={snap} />}
          {selectedId === "risk" && <RiskRaterReport snap={snap} />}
          {selectedId === "human" && <HumanAuditReport snap={snap} />}

          <AgentTimeline trace={snap.trace} agentId={selectedId} now={now} />
        </div>
      </main>
    </div>
  );
}
