"use client";

import { useEffect, useRef, useState } from "react";
import { getData } from "@/lib/api";
import { SAP_TABLES, sapCell } from "@/lib/sap";

type Row = Record<string, unknown>;

/** Side panel: the clean data the Watcher + Ingest built, with SAP table and field names. */
export default function DataDrawer({ onClose }: { onClose: () => void }) {
  const [counts, setCounts] = useState<Record<string, number>>({});
  const [table, setTable] = useState(SAP_TABLES[0].table);
  const [rows, setRows] = useState<Row[]>([]);
  const [query, setQuery] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const closeRef = useRef<HTMLButtonElement>(null);
  const spec = SAP_TABLES.find((t) => t.table === table) ?? SAP_TABLES[0];

  useEffect(() => {
    closeRef.current?.focus();
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    getData<{ table: string; rows: number }[]>("/api/truth")
      .then((list) => setCounts(Object.fromEntries(list.map((t) => [t.table, t.rows]))))
      .catch((e: Error) => setError(e.message));
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  useEffect(() => {
    let alive = true;
    const q = query.trim() ? `&q=${encodeURIComponent(query.trim())}` : "";
    const timer = setTimeout(() => {
      getData<Row[]>(`/api/truth/${table}?limit=2000${q}`)
        .then((r) => {
          if (alive) {
            setRows(r);
            setError("");
          }
        })
        .catch((e: Error) => alive && setError(e.message))
        .finally(() => alive && setLoading(false));
    }, 200);
    return () => {
      alive = false;
      clearTimeout(timer);
    };
  }, [table, query]);

  const pick = (t: string) => {
    setLoading(true);
    setTable(t);
  };

  return (
    <div className="fixed inset-0 z-50 flex justify-end bg-ink/30" onClick={onClose}>
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="data-title"
        className="flex h-full w-full max-w-[1200px] flex-col bg-surface shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-start gap-4 border-b border-rule px-5 py-4">
          <div className="min-w-0">
            <h2 id="data-title" className="text-xl font-bold">
              Portal data in SAP format
            </h2>
            <p className="text-sm text-muted">
              What the Watcher fetched and Ingest cleaned into one set of tables. Every row keeps the file it came from.
            </p>
          </div>
          <button
            ref={closeRef}
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
                    onClick={() => pick(t.table)}
                    aria-current={t.table === table}
                    className={`block w-full whitespace-nowrap px-4 py-2 text-left text-sm ${
                      t.table === table ? "bg-act-soft font-semibold text-act" : "hover:bg-dock"
                    }`}
                  >
                    <span className="block">{t.label}</span>
                    <span className="block text-xs text-muted tabular">
                      {t.sap || "no SAP table"}
                      {counts[t.table] != null && `, ${counts[t.table].toLocaleString("en-US")} rows`}
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
                <span className="text-muted">Filter</span>
                <input
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  placeholder="PO, material, vendor…"
                  className="w-56 rounded-md border border-rule px-2 py-1"
                />
              </label>
              <span className="text-sm text-muted tabular">{loading ? "Loading" : `${rows.length} rows`}</span>
            </div>
            {error && <p className="px-5 pb-3 text-sm text-stop">{error}</p>}
            <div className="min-h-0 flex-1 overflow-auto border-t border-rule">
              <table className="w-max min-w-full text-left text-sm">
                <thead className="sticky top-0 bg-dock">
                  <tr>
                    {spec.fields.map((fd) => (
                      <th key={fd.key} scope="col" className="border-b border-rule px-3 py-2 align-bottom">
                        <span className="block font-bold">{fd.sap || fd.label}</span>
                        {fd.sap && <span className="block text-xs font-normal text-muted">{fd.label}</span>}
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
                      <td className="max-w-[360px] truncate px-3 py-1.5 text-xs text-muted" title={String(r.source ?? "")}>
                        {String(r.source ?? "")}
                      </td>
                    </tr>
                  ))}
                  {!loading && rows.length === 0 && (
                    <tr>
                      <td colSpan={spec.fields.length + 1} className="px-5 py-8 text-muted">
                        {query ? "No rows match the filter." : "No rows yet. Play a data drop first."}
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </section>
        </div>
      </div>
    </div>
  );
}
