"use client";

// Planning-sheet table + its header and toolbar (left pane of the agent workspace).
import { ArrowUpDown, Download, ListFilter, Menu, Search, X } from "lucide-react";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { cn } from "@/lib/utils";

export type SheetTone = "red" | "amber" | "green" | "ai" | "none";

export interface SheetColumn<T> {
  key: string;
  label: string;
  /** Header tooltip: what the column means and where it comes from. */
  hint?: string;
  align?: "left" | "right";
  /** Width class for the <col>, e.g. "w-[112px]". */
  width?: string;
  /** Colour band for the cell (traffic light or the AI column). */
  band?: (row: T) => SheetTone;
  cell: (row: T) => ReactNode;
  /** Plain value for the CSV export. */
  csv: (row: T) => string | number;
}

const BAND: Record<SheetTone, string> = {
  red: "bg-signal-red-bg text-signal-red",
  amber: "bg-signal-amber-bg text-signal-amber",
  green: "bg-signal-green-bg text-signal-green",
  ai: "bg-ai-bg text-ai",
  none: "",
};

export function SheetTable<T>({
  rows,
  columns,
  rowId,
  highlight,
  highlightSeq,
  scanning,
  caption,
  empty,
}: {
  rows: T[];
  columns: SheetColumn<T>[];
  rowId: (row: T) => string;
  highlight: Set<string>;
  /** Bumps with every new answer so the glow replays. */
  highlightSeq: number;
  /** The agent is working: sweep a soft band down the table. */
  scanning: boolean;
  caption: string;
  empty: ReactNode;
}) {
  const scrollRef = useRef<HTMLDivElement>(null);

  // Bring the first row the answer is about into view (inside the sheet only, never the page).
  useEffect(() => {
    if (!highlightSeq) return;
    const box = scrollRef.current;
    const row = box?.querySelector<HTMLElement>("tr[data-highlight]");
    if (!box || !row) return;
    const header = box.querySelector("thead")?.getBoundingClientRect().height ?? 0;
    const b = box.getBoundingClientRect();
    const r = row.getBoundingClientRect();
    if (r.top < b.top + header || r.bottom > b.bottom) {
      const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
      box.scrollTo({ top: box.scrollTop + (r.top - b.top) - header - 8, behavior: reduce ? "auto" : "smooth" });
    }
  }, [highlightSeq]);

  return (
    <div className="relative min-h-0 flex-1">
      <div ref={scrollRef} className="absolute inset-0 overflow-auto px-3 sm:px-4">
        {/* min-w: below this the columns would squeeze to nothing; scroll sideways instead. */}
        <table className="w-full min-w-[600px] table-fixed border-collapse text-[15px]">
          <caption className="sr-only">{caption}</caption>
          <colgroup>
            {columns.map((c) => (
              <col key={c.key} className={c.width} />
            ))}
          </colgroup>
          <thead className="sticky top-0 z-10 bg-card">
            <tr className="border-b border-border">
              {columns.map((c) => (
                <th
                  key={c.key}
                  scope="col"
                  title={c.hint}
                  className={cn(
                    "whitespace-nowrap px-3 py-3 text-[13px] font-medium tracking-[0.01em] text-muted-foreground",
                    c.align === "right" ? "text-right" : "text-left",
                    c.hint && "cursor-help",
                  )}
                >
                  {c.label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => {
              const id = rowId(row);
              const lit = highlight.has(id);
              return (
                <tr
                  // A new key on lit rows restarts the glow animation for each answer.
                  key={lit ? `${id}#${highlightSeq}` : id}
                  data-highlight={lit || undefined}
                  className={cn(
                    "border-b border-border/80 transition-colors duration-150 hover:bg-secondary/70",
                    lit && "agent-row-flash",
                  )}
                >
                  {columns.map((c, i) => (
                    <td
                      key={c.key}
                      className={cn(
                        "h-[52px] px-3 align-middle",
                        c.align === "right" ? "text-right tabular-nums" : "text-left",
                        BAND[c.band?.(row) ?? "none"],
                        lit && i === 0 && "shadow-[inset_3px_0_0_var(--color-primary)]",
                      )}
                    >
                      {c.cell(row)}
                    </td>
                  ))}
                </tr>
              );
            })}
          </tbody>
        </table>
        {rows.length === 0 && <div className="px-4 py-12 text-center text-sm text-muted-foreground">{empty}</div>}
      </div>
      {/* Over the visible rows only (below the sticky header), however long the table is. */}
      {scanning && <div aria-hidden className="agent-scan pointer-events-none absolute inset-x-0 bottom-0 top-11" />}
    </div>
  );
}

/** Placeholder rows while the truth store loads. */
export function SheetSkeleton({ rows = 8 }: { rows?: number }) {
  return (
    <div className="space-y-0 px-7 pt-2" aria-hidden>
      {Array.from({ length: rows }, (_, i) => (
        <div key={i} className="flex h-[52px] items-center gap-4 border-b border-border/70">
          <span className="h-3 w-24 animate-pulse rounded bg-secondary" />
          <span className="h-3 flex-1 animate-pulse rounded bg-secondary" style={{ animationDelay: `${i * 70}ms` }} />
          <span className="h-3 w-16 animate-pulse rounded bg-secondary" />
          <span className="h-3 w-16 animate-pulse rounded bg-secondary" />
        </div>
      ))}
    </div>
  );
}

export interface SheetView {
  id: string;
  label: string;
}

/** Title + Export, then the toolbar: view menu on the left, search / sort / filter on the right. */
export function SheetHeader({
  title,
  onExport,
  exportDisabled,
  views,
  view,
  onView,
  search,
  onSearch,
  searchLabel,
  sortLabel,
  sortFlipped,
  onSort,
  filterLabel,
  filterOn,
  onFilter,
}: {
  title: string;
  onExport: () => void;
  exportDisabled?: boolean;
  views: SheetView[];
  view: string;
  onView: (id: string) => void;
  search: string;
  onSearch: (q: string) => void;
  searchLabel: string;
  sortLabel: string;
  sortFlipped: boolean;
  onSort: () => void;
  filterLabel: string;
  filterOn: boolean;
  onFilter: () => void;
}) {
  const [menuOpen, setMenuOpen] = useState(false);
  const [searchOpen, setSearchOpen] = useState(search.length > 0);
  const menuRef = useRef<HTMLDivElement>(null);
  const searchRef = useRef<HTMLInputElement>(null);
  const viewButtonRef = useRef<HTMLButtonElement>(null);
  /** Closing the search unmounts the focused input: put focus back on the view button. */
  const restoreFocus = useRef(false);
  const current = views.find((v) => v.id === view) ?? views[0];
  const closeSearch = () => {
    onSearch("");
    restoreFocus.current = true;
    setSearchOpen(false);
  };

  useEffect(() => {
    if (!menuOpen) return;
    const close = (e: MouseEvent | KeyboardEvent) => {
      if (e instanceof KeyboardEvent ? e.key === "Escape" : !menuRef.current?.contains(e.target as Node)) {
        setMenuOpen(false);
      }
    };
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", close);
    return () => {
      document.removeEventListener("mousedown", close);
      document.removeEventListener("keydown", close);
    };
  }, [menuOpen]);

  useEffect(() => {
    if (searchOpen) searchRef.current?.focus();
    else if (restoreFocus.current) {
      restoreFocus.current = false;
      viewButtonRef.current?.focus();
    }
  }, [searchOpen]);

  const iconBtn =
    "flex size-9 items-center justify-center rounded-lg text-muted-foreground outline-none transition-colors hover:bg-secondary hover:text-foreground focus-visible:ring-[3px] focus-visible:ring-ring/40";

  return (
    <>
      <div className="flex items-start justify-between gap-4 px-6 pt-6 sm:px-8 sm:pt-7">
        <h2 className="text-[28px] font-bold leading-tight tracking-[-0.02em] text-foreground sm:text-[32px]">{title}</h2>
        <button
          type="button"
          onClick={onExport}
          disabled={exportDisabled}
          className="inline-flex shrink-0 items-center gap-2 rounded-xl border bg-card px-4 py-2 text-[15px] font-medium text-foreground shadow-[0_1px_1px_rgba(16,24,20,0.04)] outline-none transition-colors hover:bg-secondary focus-visible:ring-[3px] focus-visible:ring-ring/40 disabled:opacity-50"
        >
          <Download aria-hidden className="size-4" />
          Export
        </button>
      </div>

      <div className="mt-4 flex items-center justify-between gap-2 px-4 pb-1.5 sm:mt-5 sm:px-5">
        <div ref={menuRef} className="relative min-w-0">
          {searchOpen ? (
            <div className="flex items-center gap-1.5 rounded-lg border bg-card px-2.5 focus-within:ring-[3px] focus-within:ring-ring/30">
              <Search aria-hidden className="size-4 text-muted-foreground" />
              <input
                ref={searchRef}
                type="search"
                value={search}
                onChange={(e) => onSearch(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Escape") closeSearch();
                }}
                aria-label={searchLabel}
                placeholder={searchLabel}
                className="h-9 w-44 min-w-0 bg-transparent text-[15px] outline-none placeholder:text-muted-foreground focus-visible:outline-none sm:w-56"
              />
              <button
                type="button"
                aria-label="Close search"
                onClick={closeSearch}
                className="rounded p-0.5 text-muted-foreground hover:text-foreground"
              >
                <X aria-hidden className="size-4" />
              </button>
            </div>
          ) : (
            <button
              ref={viewButtonRef}
              type="button"
              aria-haspopup="menu"
              aria-expanded={menuOpen}
              onClick={() => setMenuOpen((o) => !o)}
              // A button sizes to its text; max-w-full lets the label truncate instead of running under the icons on phones.
              className="flex max-w-full items-center gap-3 rounded-lg px-2 py-1.5 text-[17px] text-foreground outline-none transition-colors hover:bg-secondary focus-visible:ring-[3px] focus-visible:ring-ring/40"
            >
              <Menu aria-hidden className="size-[18px] shrink-0 text-muted-foreground" />
              <span className="truncate">{current?.label}</span>
            </button>
          )}
          {menuOpen && (
            <div
              role="menu"
              className="absolute left-0 top-full z-30 mt-1.5 w-56 rounded-xl border bg-popover p-1.5 shadow-lg animate-in fade-in slide-in-from-top-1 duration-150"
            >
              {views.map((v) => (
                <button
                  key={v.id}
                  type="button"
                  role="menuitemradio"
                  aria-checked={v.id === view}
                  onClick={() => {
                    onView(v.id);
                    setMenuOpen(false);
                  }}
                  className={cn(
                    "flex w-full items-center justify-between rounded-lg px-3 py-2 text-left text-sm outline-none hover:bg-secondary focus-visible:bg-secondary",
                    v.id === view && "font-semibold text-primary",
                  )}
                >
                  {v.label}
                  {v.id === view && <span aria-hidden className="size-1.5 rounded-full bg-primary" />}
                </button>
              ))}
            </div>
          )}
        </div>

        <div className="flex shrink-0 items-center gap-1">
          <button
            type="button"
            aria-label={searchLabel}
            aria-pressed={searchOpen}
            onClick={() => setSearchOpen((o) => !o)}
            className={cn(iconBtn, searchOpen && "bg-brand-soft text-primary")}
          >
            <Search aria-hidden className="size-[18px]" />
          </button>
          <button
            type="button"
            aria-label={sortLabel}
            title={sortLabel}
            onClick={onSort}
            className={cn(iconBtn, "bg-brand-soft text-primary hover:bg-brand-soft")}
          >
            <ArrowUpDown aria-hidden className={cn("size-[18px] transition-transform duration-300", sortFlipped && "rotate-180")} />
          </button>
          <button
            type="button"
            aria-label={filterLabel}
            title={filterLabel}
            aria-pressed={filterOn}
            onClick={onFilter}
            className={cn(iconBtn, filterOn && "bg-brand-soft text-primary")}
          >
            <ListFilter aria-hidden className="size-[18px]" />
          </button>
        </div>
      </div>
    </>
  );
}

/** Download rows as CSV (client-side). */
export function downloadCsv<T>(filename: string, columns: SheetColumn<T>[], rows: T[]) {
  const quote = (v: string | number) => {
    const s = String(v ?? "");
    return /[",\n]/.test(s) ? `"${s.replaceAll('"', '""')}"` : s;
  };
  const lines = [columns.map((c) => quote(c.label)).join(","), ...rows.map((r) => columns.map((c) => quote(c.csv(r))).join(","))];
  // BOM so Excel reads UTF-8 (dashes, umlauts) correctly.
  const url = URL.createObjectURL(new Blob(["﻿" + lines.join("\n")], { type: "text/csv;charset=utf-8" }));
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  // Revoking right away can cancel the download in some browsers.
  setTimeout(() => URL.revokeObjectURL(url), 2000);
}
