"use client";

import { useEffect, useState } from "react";
import type { AgentLogEntry, DecisionHistory } from "../../API_CONTRACT";
import { API_URL, getData, parseTs } from "@/lib/api";

const agents: Record<string, string> = {
  watcher: "Watcher", ingest: "Ingest", reconciler: "Reconciler", risk: "Risk Rater", planner: "Planner", chaser: "Chaser", policy: "Company policy",
};

function TraceRecord({ id }: { id: number }) {
  const [record, setRecord] = useState<AgentLogEntry>();
  const [error, setError] = useState("");
  const load = async () => {
    if (record) return;
    setError("");
    try {
      const [row] = await getData<AgentLogEntry[]>(`/api/trace?trace_id=${id}`);
      if (row) setRecord(row);
      else setError("No matching trace entry.");
    } catch (e) { setError((e as Error).message); }
  };
  return <details className="mt-1 text-xs" onToggle={e => { if (e.currentTarget.open) void load(); }}>
    <summary className="cursor-pointer text-act underline">Trace #{id}</summary>
    {error ? <p role="alert">{error} <button className="underline" onClick={load}>Retry</button></p> :
      record ? <div className="my-1 border-l border-rule pl-2">
        <p className="font-semibold">{agents[record.agent] || record.agent} · {record.action.replaceAll("_", " ")}</p>
        <p>{record.detail}</p>
        <p className="text-muted">Run {record.run_id} · Logged {parseTs(record.ts)?.toLocaleString("en-GB", {timeZone:"UTC"})} UTC</p>
        <a className="text-act underline" href={API_URL + "/api/trace?trace_id=" + id} target="_blank" rel="noreferrer">Raw record ↗</a>
      </div> : <p role="status">Loading trace…</p>}
  </details>;
}

export default function History({ decision, option, activity }: { decision: number; option?: number; activity?: string }) {
  const [open, setOpen] = useState(false);
  const [history, setHistory] = useState<DecisionHistory | null>(null);
  const [error, setError] = useState("");
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    if (!open) return;
    let current = true;
    getData<DecisionHistory>(`/api/decisions/${decision}/history${option == null ? "" : "?option=" + option}`)
      .then(data => { if (current) { setHistory(data); setError(""); } })
      .catch((e: Error) => { if (current) setError(e.message); });
    return () => { current = false; };
  }, [open, decision, option, activity, attempt]);

  return <details className="my-2 text-sm" onToggle={event => setOpen(event.currentTarget.open)}>
    <summary className="cursor-pointer font-semibold text-act">Decision history</summary>
    {open && error && <p role="alert" className="mt-2 text-warn">History unavailable. {error}{" "}
      <button className="underline" onClick={() => setAttempt(attempt + 1)}>Retry</button></p>}
    {open && !error && !history && <p role="status">Loading history…</p>}
    {open && history?.decision_id === decision && <ol className="my-3 space-y-2 border-l-2 border-rule pl-4">
      {history.events.map(e => <li key={`${e.record.type}-${e.record.id}-${e.title}`}>
        <p><strong>{e.title}</strong> · {agents[e.actor] || e.actor}</p>
        <p className="text-muted">{e.detail}</p>
        {e.trace_id && <TraceRecord id={e.trace_id} />}
      </li>)}
    </ol>}
  </details>;
}
