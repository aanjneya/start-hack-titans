"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  ago, API_URL, clock, eur, fmt, getData, parseTs, postData,
  type AgentLogEntry, type DecisionCard, type Forecast, type HealthCheckResponse, type InboxMessage,
  type Issue, type OutboxMessage, type RiskScore,
} from "@/lib/api";
import DataDrawer from "./data-drawer";

type Snapshot = {
  health: HealthCheckResponse | null;
  forecast: Forecast[];
  decisions: DecisionCard[];
  issues: Issue[];
  outbox: OutboxMessage[];
  inbox: InboxMessage[];
  risk: RiskScore[];
  trace: AgentLogEntry[];
};

const EMPTY: Snapshot = { health: null, forecast: [], decisions: [], issues: [], outbox: [], inbox: [], risk: [], trace: [] };

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
  5: { label: "Line stops", cls: "bg-stop text-white" },
  4: { label: "Copies at risk", cls: "bg-stop-soft text-stop" },
  3: { label: "Answer today", cls: "bg-warn-soft text-warn" },
  2: { label: "Watch", cls: "bg-act-soft text-act" },
  1: { label: "Info", cls: "bg-dock text-muted" },
};

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

async function loadSnapshot(): Promise<{ snap: Snapshot; online: boolean }> {
  const [health, forecast, decisions, issues, outbox, inbox, risk, trace] = await Promise.allSettled([
    getData<HealthCheckResponse>("/api/health"),
    getData<Forecast[]>("/api/forecast"),
    getData<DecisionCard[]>("/api/decisions?status=proposed"),
    getData<Issue[]>("/api/issues"),
    getData<OutboxMessage[]>("/api/outbox"),
    getData<InboxMessage[]>("/api/inbox"),
    getData<RiskScore[]>("/api/risk"),
    getData<AgentLogEntry[]>("/api/trace?limit=400"),
  ]);
  const val = <T,>(r: PromiseSettledResult<T>, fallback: T) => (r.status === "fulfilled" ? r.value : fallback);
  return {
    online: health.status === "fulfilled",
    snap: {
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
  const [online, setOnline] = useState<boolean | null>(null);
  const [now, setNow] = useState<number>(0);
  const [dropBusy, setDropBusy] = useState(false);
  const [notice, setNotice] = useState<string>("");
  const [tab, setTab] = useState<"findings" | "emails" | "risk">("findings");
  const [showData, setShowData] = useState(false);
  const closeData = useCallback(() => setShowData(false), []);

  const refresh = useCallback(async () => {
    const { snap: next, online: up } = await loadSnapshot();
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
          ? `All ${r.drops} recorded drops are in. Restart the backend to play them again.`
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

  // Cards that recommend the very same step (same headline AND same action, supplier, PO line) are shown once
  // (the newest), with a count. Same headline but another PO stays a card of its own.
  const decisionGroups = useMemo(() => {
    const groups = new Map<string, DecisionCard[]>();
    for (const d of snap.decisions) {
      const key = `${d.summary || d.title}|${actionTargets(d.options[d.recommended ?? -1]?.actions).join(";")}`;
      groups.set(key, [...(groups.get(key) ?? []), d]);
    }
    return [...groups.values()].map((cards) => ({ card: cards[0], similar: cards.slice(1) }));
  }, [snap.decisions]);

  const deadline = editions.find((f) => f.deadline)?.deadline ?? null;
  const mock = snap.health?.mockMode ?? true;

  return (
    <div className="mx-auto max-w-[1360px] px-4 pb-16 sm:px-6">
      <TopBar
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

      <Hero editions={editions} />

      <div className="mt-8 grid gap-8 lg:grid-cols-[minmax(0,1fr)_400px]">
        <section aria-labelledby="decide">
          <div className="mb-3 flex items-baseline justify-between gap-4">
            <h2 id="decide" className="text-xl font-bold">
              Decide now
            </h2>
            <span className="text-sm text-muted tabular">
              {snap.decisions.length} open {snap.decisions.length === 1 ? "card" : "cards"}
            </span>
          </div>
          {decisionGroups.length === 0 ? (
            <p className="rounded-lg border border-dashed border-rule bg-surface px-5 py-8 text-muted">
              Nothing waits for a decision. When a data drop arrives, the Planner puts its proposals here.
            </p>
          ) : (
            <ol className="space-y-4">
              {decisionGroups.map(({ card, similar }) => (
                <li key={card.id}>
                  <DecisionView card={card} similar={similar} onDone={refresh} />
                </li>
              ))}
            </ol>
          )}
        </section>

        <aside aria-labelledby="agents" className="space-y-6">
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
        {tab === "emails" && <Emails outbox={snap.outbox} inbox={snap.inbox} mock={mock} onSent={refresh} />}
        {tab === "risk" && <Risk rows={snap.risk} />}
      </section>
      {showData && <DataDrawer onClose={closeData} />}
    </div>
  );
}

function TopBar(props: {
  online: boolean | null;
  mock: boolean;
  deadline: string | null;
  now: number;
  busy: boolean;
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
    weekday: "short", hour: "2-digit", minute: "2-digit", timeZone: "Europe/Berlin",
  });
  return (
    <header className="flex flex-wrap items-center justify-between gap-4 py-5">
      <div>
        <h1 className="text-2xl font-bold leading-tight">Grand Heist 6 launch</h1>
        <p className="text-sm text-muted">
          Soony packing line, Hamburg
          {countdown && (
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
          {props.mock ? "Mock mode" : "Live"}
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
          disabled={props.busy || !props.online}
          className="rounded-md bg-ink px-4 py-2 text-sm font-semibold text-white hover:bg-act disabled:cursor-not-allowed disabled:opacity-50"
          title="Sends the next real portal drop (from git history) through all agents. The first click starts from an empty store."
        >
          {props.busy ? "Agents are working" : "Play next data drop"}
        </button>
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
          <div key={f.edition} className="bg-surface px-6 py-6">
            <p className={`figure text-[clamp(64px,10vw,124px)] ${short ? "text-stop" : "text-ok"}`}>
              {short ? fmt(f.shortfall) : "0"}
            </p>
            <p className="mt-2 text-lg font-semibold">
              {f.edition} copies {short ? "will be missing at close" : "short: on track"}
            </p>
            <p className="mt-1 text-sm text-muted tabular">
              {fmt(f.still_to_pack)} still to pack
              {f.still_to_pack != null && `, ${fmt(Math.max(0, f.still_to_pack - (f.shortfall ?? 0)))} of them by close`}
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

function DecisionView({ card, similar, onDone }: { card: DecisionCard; similar: DecisionCard[]; onDone: () => void }) {
  const [choice, setChoice] = useState<number>(card.recommended ?? 0);
  const [why, setWhy] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const option = card.options[choice];
  const approvers = option?.approvers?.length ? option.approvers : [card.approver || "Demo user"];

  const decide = async (verb: "approve" | "reject") => {
    setBusy(true);
    setError("");
    try {
      await postData(`/api/decisions/${card.id}/${verb}`, {
        by: approvers[0],
        approvals: verb === "approve" ? approvers.slice(1) : [],
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
          const off = o.available === false;
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
                className="mt-1 accent-[#1d5fb4]"
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
        <button
          onClick={() => decide("approve")}
          disabled={busy || !option || option.available === false}
          className="rounded-md bg-act px-4 py-2 text-sm font-semibold text-white hover:bg-ink disabled:opacity-50"
        >
          Approve as {approvers.join(" + ")}
        </button>
        <button
          onClick={() => decide("reject")}
          disabled={busy}
          className="rounded-md border border-rule px-4 py-2 text-sm font-semibold hover:bg-dock disabled:opacity-50"
        >
          Reject
        </button>
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
            <li key={a.id} className="px-4 py-3">
              <div className="flex items-center gap-2">
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
                <span className="ml-auto text-xs text-muted tabular">
                  {s ? ago(s.last?.ts, now) : "idle"}
                  {s && s.errors > 0 && <span className="ml-2 font-semibold text-stop">{s.errors} errors</span>}
                </span>
              </div>
              <p className="text-xs text-muted">{a.job}</p>
              {s?.last && (
                <p className="mt-1 truncate text-sm" title={s.last.detail}>
                  {s.last.detail || s.last.action}
                </p>
              )}
              <div className="mt-2 h-1 rounded bg-dock" aria-hidden>
                <div className="h-1 rounded" style={{ width: `${((s?.count ?? 0) / max) * 100}%`, background: a.color }} />
              </div>
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
  const status = m.status === "sent" && mock ? "simulated" : m.status;

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
    return `Bound to our request by Ingest: ${fmt(Number(advice.quantity))} pcs, revision ${String(advice.revision)}, arrives ${when}.`;
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
