"use client";

import { useState } from "react";
import type { SampleWindow } from "../../API_CONTRACT";
import { getData } from "@/lib/api";

const displayDate = (iso: string) => new Intl.DateTimeFormat("en-GB", {
  day: "numeric", month: "short", year: "numeric", timeZone: "UTC",
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

  return <details className="mt-3 rounded-md border border-rule text-sm">
    <summary className="cursor-pointer px-3 py-2 font-semibold text-act">Compare sample timing</summary>
    <div className="border-t border-rule p-3">
      <label className="flex flex-wrap items-center gap-2">When would you request samples?
        <select className="rounded border border-rule bg-surface p-1.5 text-ink" value={delay} disabled={busy}
          onChange={e => void compare(Number(e.target.value))}>
          <option value={0}>At the evidence snapshot</option><option value={7}>One week later</option><option value={14}>Two weeks later</option>
        </select>
      </label>
      {error && <p role="alert" className="text-stop">{error}</p>}
      <div role="status" aria-busy={busy} className="mt-3">
        <p className={scenario.days_between <= 0 ? "font-semibold text-warn" : "font-semibold"}>
          {scenario.days_between === 0 ? "Samples would coincide with the first scheduled PO delivery." :
            `Samples would be available ${Math.abs(scenario.days_between)} ${Math.abs(scenario.days_between) === 1 ? "day" : "days"} ${scenario.days_between > 0 ? "before" : "after"} the first scheduled PO delivery.`}
        </p>
        <p className="mt-1 tabular">Request {displayDate(scenario.requested_on || scenario.as_of)}
          {" · "}Samples by {displayDate(scenario.sample_by)}{" · "}First PO due {displayDate(scenario.first_delivery)}</p>
      </div>
      <p className="mt-2 text-muted">Assumes the supplier’s {window.sample_days}-day offer uses calendar days.
        Fit, production, transit and Quality release times are still open.</p>
    </div>
  </details>;
}
