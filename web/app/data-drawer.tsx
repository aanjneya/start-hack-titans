"use client";

import { useEffect, useState } from "react";
import { getData } from "@/lib/api";
import { SAP_TABLES, sapCell } from "@/lib/sap";
import ModalPanel from "./modal-panel";

type Row = Record<string, unknown>;

/** Side panel: the clean data the Watcher + Ingest built, with SAP table and field names. */
export default function DataDrawer({ onClose }: { onClose: () => void }) {
  const [counts, setCounts] = useState<Record<string, number>>({});
  const [table, setTable] = useState(SAP_TABLES[0].table);
  const [query, setQuery] = useState("");
  const [attempt, setAttempt] = useState(0);
  const [result, setResult] = useState<{ key: string; rows: Row[]; error?: string } | null>(null);
  const spec = SAP_TABLES.find((t) => t.table === table) ?? SAP_TABLES[0];
  const search = query.trim();
  const key = JSON.stringify([table, search, attempt]);
  const current = result?.key === key ? result : null;
  const rows = current?.rows ?? [];

  useEffect(() => {
    let active = true;
    getData<{ table: string; rows: number }[]>("/api/truth")
      .then(list => { if (active) setCounts(Object.fromEntries(list.map(t => [t.table, t.rows]))); })
      .catch(() => { /* Row counts are optional. The table request reports its own errors. */ });
    return () => { active = false; };
  }, []);

  useEffect(() => {
    let active = true;
    const timer = setTimeout(() => {
      getData<Row[]>(`/api/truth/${table}?limit=2000&q=${encodeURIComponent(search)}`)
        .then(rows => { if (active) setResult({key, rows}); })
        .catch((e: Error) => { if (active) setResult({key, rows: [], error: e.message}); });
    }, search ? 180 : 0);
    return () => { active = false; clearTimeout(timer); };
  }, [table, search, key]);

  return (
    <ModalPanel label="Source records" onClose={onClose}
      className="m-0 ml-auto h-dvh max-h-dvh w-full max-w-[1200px] border-l border-rule bg-surface p-0 text-ink shadow-2xl backdrop:bg-ink/30">
      <div className="flex h-full flex-col">
        <div className="flex items-start gap-4 border-b border-rule px-5 py-4">
          <div className="min-w-0">
            <h2 id="data-title" className="text-xl font-bold">
              Source records
            </h2>
            <p className="text-sm text-muted">
              Search purchasing, stock and shipment records. Each row identifies its source file.
            </p>
          </div>
          <button
            onClick={onClose}
            className="ml-auto shrink-0 rounded-md border border-rule px-3 py-1.5 text-sm font-semibold hover:bg-dock"
          >
            Close
          </button>
        </div>

        <div className="flex min-h-0 flex-1 flex-col md:flex-row">
          <nav aria-label="Tables" className="shrink-0 overflow-x-auto border-b border-rule md:w-64 md:overflow-y-auto md:border-b-0 md:border-r">
            <ul className="flex md:block">
              {SAP_TABLES.map((t) => (
                <li key={t.table}>
                  <button
                    onClick={() => setTable(t.table)}
                    aria-current={t.table === table}
                    className={`block w-full whitespace-nowrap px-4 py-2 text-left text-sm ${
                      t.table === table ? "bg-act-soft font-semibold text-act" : "hover:bg-dock"
                    }`}
                  >
                    <span className="block">{t.label}</span>
                    <span className="block text-xs text-muted tabular">
                      {t.sap}
                      {counts[t.table] != null && `${t.sap ? " · " : ""}${counts[t.table].toLocaleString("en-US")} rows`}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          </nav>

          <section className="flex min-h-0 min-w-0 flex-1 flex-col">
            <div className="flex flex-wrap items-center gap-3 px-5 py-3">
              <h3 className="font-semibold">
                {spec.label}
                {spec.sap && <span className="ml-2 font-normal text-muted">{spec.sap}</span>}
              </h3>
              <label className="ml-auto flex items-center gap-2 text-sm">
                <span className="text-muted">Search</span>
                <input
                  type="search"
                  aria-label={`Search ${spec.label.toLowerCase()}`}
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  placeholder="PO, material, vendor…"
                  className="w-48 max-w-full rounded-md border border-rule px-2 py-1"
                />
              </label>
              <span role="status" className="text-sm text-muted tabular">
                {!current ? "Loading records…" : !current.error && `${rows.length === 2000 ? "First " : ""}${rows.length.toLocaleString("en-US")} rows`}
              </span>
            </div>
            {current?.error && <p role="alert" className="px-5 pb-3 text-sm text-stop">Couldn’t load {spec.label.toLowerCase()}.{" "}
              <button type="button" className="font-semibold underline" onClick={() => setAttempt(n => n + 1)}>Retry</button>
            </p>}
            <div className="min-h-0 flex-1 overflow-auto border-t border-rule">
              <table className="w-max min-w-full text-left text-sm">
                <thead className="sticky top-0 bg-dock">
                  <tr>
                    {spec.fields.map((fd) => (
                      <th key={fd.key} scope="col" className="border-b border-rule px-3 py-2 align-bottom">
                        <span className="block font-bold">{fd.label}</span>
                        {fd.sap && <span className="block text-xs font-normal text-muted">{fd.sap}</span>}
                      </th>
                    ))}
                    <th scope="col" className="border-b border-rule px-3 py-2 align-bottom font-bold">
                      Source file
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((r, i) => (
                    <tr key={String(r.id ?? i)} className="border-b border-rule hover:bg-dock/60">
                      {spec.fields.map((fd) => (
                        <td
                          key={fd.key}
                          className={`max-w-[320px] truncate px-3 py-1.5 ${typeof r[fd.key] === "number" ? "text-right tabular" : ""}`}
                          title={sapCell(r[fd.key])}
                        >
                          {sapCell(r[fd.key])}
                        </td>
                      ))}
                      <td
                        className="max-w-[360px] truncate px-3 py-1.5 text-xs text-muted"
                        title={
                          r.last_source && r.last_source !== r.source
                            ? `First from ${String(r.source)}; last changed by ${String(r.last_source)}`
                            : String(r.source ?? "")
                        }
                      >
                        {String(r.last_source || r.source || "")}
                        {r.last_source && r.last_source !== r.source ? " (updated)" : ""}
                      </td>
                    </tr>
                  ))}
                  {current && !current.error && rows.length === 0 && (
                    <tr>
                      <td colSpan={spec.fields.length + 1} className="px-5 py-8 text-muted">
                        {search ? <>No records match “{search}”.{" "}
                          <button type="button" className="text-act underline" onClick={() => setQuery("")}>Clear search</button></> : "This table has no records yet."}
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </section>
        </div>
      </div>
    </ModalPanel>
  );
}
