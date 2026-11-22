"use client";

import { useState } from "react";
import type { SampleWindow } from "../../API_CONTRACT";
import { getData } from "@/lib/api";

const displayDate = (iso: string) => new Intl.DateTimeFormat("en-GB", {
  day: "numeric", month: "short", timeZone: "UTC",
}).format(new Date(iso + "T00:00:00Z"));

export default function SampleTiming({ decision, option, window }: { decision: number; option: number; window: SampleWindow }) {
  const [scenario, setScenario] = useState(window);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [delay, setDelay] = useState(0);
  const compare = async (days: number) => {
    setBusy(true);
    setError("");
    const date = new Date(window.as_of + "T00:00:00Z");
    date.setUTCDate(date.getUTCDate() + days);
    try {
      const next = days === 0 ? window : await getData<SampleWindow>(
        `/api/decisions/${decision}/sample-window?option=${option}&requested_on=${date.toISOString().slice(0, 10)}`);
      setScenario(next);
      setDelay(days);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const milestones = [
    {label: "Request samples", date: scenario.requested_on || scenario.as_of},
    {label: "Samples available", date: scenario.sample_by},
    {label: "First PO due", date: scenario.first_delivery},
  ].sort((a, b) => a.date.localeCompare(b.date));
  const gap = Math.abs(scenario.days_between);
  const relative = scenario.days_between > 0 ? "before" : "after";
  return <details className="mt-3 rounded-md border border-rule text-sm">
    <summary className="cursor-pointer px-3 py-2 font-semibold text-act">Compare sample timing
      <span className="ml-2 font-normal text-muted">{window.days_between > 0 ?
        `${window.days_between} ${window.days_between === 1 ? "day" : "days"} to assess samples if requested now`
        : "Check the first PO date"}</span></summary>
    <div className="border-t border-rule p-3">
      <fieldset disabled={busy}>
        <legend className="font-semibold">When would you request samples?</legend>
        <div className="mt-2 flex flex-wrap gap-1.5">{[0, 7, 14].map(days =>
          <button key={days} type="button" aria-pressed={delay === days} onClick={() => void compare(days)}
            className={`rounded border px-3 py-1.5 font-medium ${delay === days
              ? "border-act bg-act-soft text-act" : "border-rule bg-surface"}`}>
            {days === 0 ? "At snapshot" : days === 7 ? "One week later" : "Two weeks later"}
          </button>)}</div>
      </fieldset>
      {error && <p role="alert" className="text-stop">{error}</p>}
      <div role="status" aria-busy={busy} className="mt-3">
        <p className={scenario.days_between <= 0 ? "font-semibold text-warn" : "font-semibold"}>
          {scenario.days_between === 0 ? "Samples would coincide with the first scheduled PO delivery." :
            `Samples would be available ${gap} ${gap === 1 ? "day" : "days"} ${relative} the first scheduled PO delivery.`}
        </p>
        <ol className="my-3 grid grid-cols-3 gap-2" aria-label="Dates in order">
          {milestones.map(m => <li key={m.label} className={`min-w-0 border-t-2 pt-1.5 ${m.label === "First PO due" ? "border-warn" : "border-act"}`}>
            <time dateTime={m.date} title={m.date} className="block font-bold tabular">{displayDate(m.date)}</time>
            <span className="text-xs text-muted">{m.label}</span>
          </li>)}
        </ol>
      </div>
      <p className="mt-2 text-muted">Assumes the supplier’s {window.sample_days}-day offer uses calendar days.
        Fit, production, transit and Quality release times are still open.</p>
    </div>
  </details>;
}
