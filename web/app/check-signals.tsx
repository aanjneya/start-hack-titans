"use client";

import { useState } from "react";
import { RefreshCw } from "lucide-react";
import type { ActionRun } from "../../API_CONTRACT";
import { API_URL, postData } from "@/lib/api";

export default function CheckSignals({ online, mock, refresh }: {
  online: boolean; mock: boolean; refresh: () => Promise<void>;
}) {
  const [busy, setBusy] = useState(false);
  const [run, setRun] = useState<ActionRun | null>(null);
  const [error, setError] = useState("");
  async function check() {
    setBusy(true);
    setError("");
    setRun(null);
    try {
      const result = await postData<ActionRun>("/api/risk/refresh");
      setRun(result);
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "The signal check failed.");
    } finally {
      setBusy(false);
    }
  }
  return <div className="flex flex-wrap items-center gap-2">
    <button type="button" onClick={check} disabled={!online || busy} className="inline-flex min-h-10 items-center gap-2 rounded-md border border-rule bg-surface px-3 py-2 text-sm font-semibold text-brand-ink hover:bg-act-soft disabled:opacity-60">
      <RefreshCw size={15} aria-hidden className={busy ? "motion-safe:animate-spin" : undefined} />
      {busy ? "Checking signals…" : mock ? "Check signals" : "Refresh live signals"}
    </button>
    <span role="status" className={`text-xs ${error || run?.status === "partial" ? "text-warn" : "text-muted"}`}>
      {error || (run && <>{run.status === "partial" ? "Some sources need review." : "Check completed."}{" "}
        <a href={API_URL + "/api/trace?run_id=" + encodeURIComponent(run.run_id)} target="_blank" rel="noreferrer" className="underline">Check trace (JSON)</a></>)}
    </span>
  </div>;
}
