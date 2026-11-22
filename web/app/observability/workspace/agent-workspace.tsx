"use client";

// Watcher / Ingest workspace: a planning sheet on the left, a chat with the agent on the right.
import { useCallback, useMemo, useState, type ReactNode } from "react";
import { clock, fmt, type ObservabilitySnapshot } from "@/lib/api";
import { cn } from "@/lib/utils";
import { ChatPanel } from "./chat-panel";
import {
  buildFileRows,
  buildPartRows,
  coverTone,
  newestForecasts,
  useTruthData,
  type FileRow,
  type PartRow,
  type TruthData,
} from "./data";
import { SheetHeader, SheetSkeleton, SheetTable, downloadCsv, type SheetColumn, type SheetView } from "./sheet";
import type { BrainContext, WorkspaceAgent } from "./types";

interface Highlight {
  ids: Set<string>;
  seq: number;
}

export function AgentWorkspace({ agent, snap }: { agent: WorkspaceAgent; snap: ObservabilitySnapshot }) {
  // Reload the truth store when a drop lands or Ingest logs new changes.
  const version = useMemo(
    () => `${snap.drops.map((d) => d.id).join(",")}|${snap.changes[0]?.id ?? 0}|${snap.issues.length}|${snap.forecast[0]?.id ?? 0}`,
    [snap.drops, snap.changes, snap.issues.length, snap.forecast],
  );
  const truth = useTruthData(version);
  const [scanning, setScanning] = useState(false);
  const [highlight, setHighlight] = useState<Highlight>({ ids: new Set(), seq: 0 });

  const onHighlight = useCallback((ids: string[]) => {
    setHighlight((h) => ({ ids: new Set(ids), seq: ids.length ? h.seq + 1 : h.seq }));
  }, []);

  const ctx = useMemo<BrainContext>(() => ({ agent, snap, truth: truth.data }), [agent, snap, truth.data]);
  const label = agent === "ingest" ? "Ingest" : "Watcher";

  return (
    <section
      aria-label={`${label} agent workspace`}
      className="grid grid-cols-[minmax(0,1fr)] overflow-hidden rounded-2xl border border-rule bg-card shadow-[0_1px_2px_rgba(16,24,20,0.04),0_8px_24px_-12px_rgba(16,24,20,0.08)] min-[1400px]:h-[min(880px,calc(100vh-132px))] min-[1400px]:min-h-[620px] min-[1400px]:grid-cols-[minmax(0,1fr)_400px] 2xl:grid-cols-[minmax(0,1fr)_minmax(460px,0.75fr)]"
    >
      <div className="flex min-h-0 flex-col max-[1399px]:h-[640px]">
        {agent === "ingest" ? (
          <PartsSheet
            truth={truth.data}
            error={truth.error}
            onRetry={truth.reload}
            snap={snap}
            highlight={highlight}
            scanning={scanning}
          />
        ) : (
          <FilesSheet truth={truth.data} snap={snap} highlight={highlight} scanning={scanning} />
        )}
      </div>
      <div className="flex min-h-0 flex-col border-t border-rule bg-pane max-[1399px]:h-[720px] min-[1400px]:border-l min-[1400px]:border-t-0">
        <ChatPanel ctx={ctx} agentLabel={label} onWorkingChange={setScanning} onHighlight={onHighlight} />
      </div>
    </section>
  );
}

// ---------------------------------------------------------------------------
// Ingest: launch parts
// ---------------------------------------------------------------------------
const PART_VIEWS: SheetView[] = [
  { id: "launch", label: "Used in the launch" },
  { id: "order", label: "To order" },
  { id: "critical", label: "Critical parts" },
  { id: "all", label: "All parts" },
];

const muted0 = <span className="opacity-40">0</span>;

const PART_COLUMNS: SheetColumn<PartRow>[] = [
  {
    key: "id",
    label: "Part",
    width: "w-[140px]",
    cell: (r) => (
      <span className="flex items-center gap-1.5 whitespace-nowrap font-medium text-foreground">
        {r.id}
        {r.openFindings > 0 && (
          <span
            title={`${r.openFindings} open finding${r.openFindings === 1 ? "" : "s"}: ${r.topFinding ?? ""}`}
            className={cn(
              "size-1.5 shrink-0 rounded-full",
              r.topSeverity >= 5 ? "bg-signal-red" : r.topSeverity >= 3 ? "bg-signal-amber" : "bg-muted-foreground/50",
            )}
          />
        )}
      </span>
    ),
    csv: (r) => r.id,
  },
  {
    key: "description",
    label: "Description",
    cell: (r) => (
      <span className="block truncate text-muted-foreground" title={r.description}>
        {r.description}
      </span>
    ),
    csv: (r) => r.description,
  },
  {
    key: "stock",
    label: "Free stock",
    hint: "All stock lots in the ERP stock snapshot",
    align: "right",
    width: "w-[96px]",
    cell: (r) => (r.freeStock ? fmt(r.freeStock) : muted0),
    csv: (r) => r.freeStock,
  },
  {
    key: "po",
    label: "On PO",
    hint: "Open purchase-order quantity due by the launch close",
    align: "right",
    width: "w-[84px]",
    cell: (r) => (r.onPoByClose ? fmt(r.onPoByClose) : muted0),
    csv: (r) => r.onPoByClose,
  },
  {
    key: "cover",
    label: "Weeks cover",
    hint: "From the ERP stock snapshot · red under 2 weeks, amber under 4",
    align: "right",
    width: "w-[108px]",
    band: (r) => coverTone(r.weeksCover),
    cell: (r) => (r.weeksCover == null ? "–" : r.weeksCover.toFixed(1)),
    csv: (r) => r.weeksCover ?? "",
  },
  {
    key: "ai",
    label: "AI order",
    hint: "Units to expedite so every launch copy is packed by close: the Launch Planner's shortfall × units per copy",
    align: "right",
    width: "w-[92px]",
    band: () => "ai",
    cell: (r) => (r.aiOrder ? fmt(r.aiOrder) : muted0),
    csv: (r) => r.aiOrder,
  },
];

function PartsSheet({
  truth,
  error,
  onRetry,
  snap,
  highlight,
  scanning,
}: {
  truth: TruthData | null;
  error: string | null;
  onRetry: () => void;
  snap: ObservabilitySnapshot;
  highlight: Highlight;
  scanning: boolean;
}) {
  const [view, setView] = useState("launch");
  const [search, setSearch] = useState("");
  const [desc, setDesc] = useState(false);
  const [withFindings, setWithFindings] = useState(false);

  const all = useMemo(() => buildPartRows(truth, snap), [truth, snap]);
  const rows = useMemo(() => {
    const q = search.trim().toLowerCase();
    const out = all.filter(
      (r) =>
        (view === "all" ||
          (view === "launch" && r.editions.length > 0) ||
          (view === "order" && r.editions.length > 0 && (r.aiOrder > 0 || coverTone(r.weeksCover) === "red")) ||
          (view === "critical" && r.critical)) &&
        (!withFindings || r.openFindings > 0) &&
        (!q || r.id.toLowerCase().includes(q) || r.description.toLowerCase().includes(q)),
    );
    return desc ? [...out].reverse() : out;
  }, [all, view, search, desc, withFindings]);

  useRevealHighlight(highlight, rows, all, (r) => r.id, () => {
    setView(all.some((r) => highlight.ids.has(r.id) && r.editions.length === 0) ? "all" : "launch");
    setSearch("");
    setWithFindings(false);
  });

  const snapshotDate = truth?.stock[0]?.snapshot_date;
  const asOf = newestForecasts(snap.forecast)[0]?.details?.as_of as string | undefined;

  return (
    <>
      <SheetHeader
        title="Launch parts"
        onExport={() => downloadCsv("launch-parts.csv", PART_COLUMNS, rows)}
        exportDisabled={rows.length === 0}
        views={PART_VIEWS}
        view={view}
        onView={setView}
        search={search}
        onSearch={setSearch}
        searchLabel="Search parts"
        sortLabel={desc ? "Sorted by weeks cover, highest first" : "Sorted by weeks cover, lowest first"}
        sortFlipped={desc}
        onSort={() => setDesc((d) => !d)}
        filterLabel={withFindings ? "Showing parts with open findings · show all" : "Only parts with open findings"}
        filterOn={withFindings}
        onFilter={() => setWithFindings((f) => !f)}
      />
      {!truth && !error ? (
        <SheetSkeleton />
      ) : !truth && error ? (
        <SheetError message={error} onRetry={onRetry} />
      ) : (
        <SheetTable
          rows={rows}
          columns={PART_COLUMNS}
          rowId={(r) => r.id}
          highlight={highlight.ids}
          highlightSeq={highlight.seq}
          scanning={scanning}
          caption="Launch parts: stock, open POs, weeks of cover and the AI order"
          empty={all.length ? "No parts match this view." : "No parts in the truth store yet. Play the next data drop."}
        />
      )}
      <SheetFooter>
        {fmt(rows.length)} of {fmt(all.length)} parts
        {snapshotDate ? ` · stock snapshot ${snapshotDate}` : ""}
        {asOf ? ` · forecast as of ${clock(asOf)}` : ""}
      </SheetFooter>
    </>
  );
}

// ---------------------------------------------------------------------------
// Watcher: portal files
// ---------------------------------------------------------------------------
const FILE_VIEWS: SheetView[] = [
  { id: "all", label: "All files" },
  { id: "latest", label: "Latest drop" },
  { id: "changed", label: "Changed or removed" },
];

const CHANGE_LABEL: Record<FileRow["change"], string> = { added: "Added", changed: "Changed", removed: "Removed" };

const FILE_COLUMNS: SheetColumn<FileRow>[] = [
  {
    key: "name",
    label: "File",
    width: "w-[36%]",
    cell: (r) => (
      <span className="block truncate font-medium text-foreground" title={`${r.path} · ${r.kind}`}>
        {r.name}
      </span>
    ),
    csv: (r) => r.path,
  },
  {
    key: "folder",
    label: "Folder",
    cell: (r) => (
      <span className="block truncate text-muted-foreground" title={r.folder}>
        {r.folder}
      </span>
    ),
    csv: (r) => r.folder,
  },
  {
    key: "drop",
    label: "Drop",
    width: "w-[112px]",
    cell: (r) => <span className="whitespace-nowrap tabular-nums">{r.dropLabel}</span>,
    csv: (r) => r.dropLabel,
  },
  {
    key: "change",
    label: "Change",
    hint: "What the Watcher saw in that drop · green added, amber changed, red removed",
    align: "right",
    width: "w-[96px]",
    band: (r) => (r.change === "added" ? "green" : r.change === "changed" ? "amber" : "red"),
    cell: (r) => CHANGE_LABEL[r.change],
    csv: (r) => r.change,
  },
  {
    key: "rows",
    label: "Rows read",
    hint: "Rows the Ingest agent wrote or updated from this file in the truth store",
    align: "right",
    width: "w-[96px]",
    band: () => "ai",
    cell: (r) => (r.rowsRead == null ? <span className="opacity-40">…</span> : r.rowsRead ? fmt(r.rowsRead) : muted0),
    csv: (r) => r.rowsRead ?? "",
  },
];

function FilesSheet({
  truth,
  snap,
  highlight,
  scanning,
}: {
  truth: TruthData | null;
  snap: ObservabilitySnapshot;
  highlight: Highlight;
  scanning: boolean;
}) {
  const [view, setView] = useState("all");
  const [search, setSearch] = useState("");
  const [oldestFirst, setOldestFirst] = useState(false);
  const [readOnly, setReadOnly] = useState(false);

  const all = useMemo(() => buildFileRows(truth, snap), [truth, snap]);
  const latestDrop = useMemo(() => Math.max(0, ...snap.drops.map((d) => d.id)), [snap.drops]);
  const rows = useMemo(() => {
    const q = search.trim().toLowerCase();
    const out = all.filter(
      (r) =>
        (view === "all" || (view === "latest" && r.dropId === latestDrop) || (view === "changed" && r.change !== "added")) &&
        (!readOnly || (r.rowsRead ?? 0) > 0) &&
        (!q || r.path.toLowerCase().includes(q) || r.folder.toLowerCase().includes(q)),
    );
    return oldestFirst ? [...out].reverse() : out;
  }, [all, view, search, oldestFirst, readOnly, latestDrop]);

  useRevealHighlight(highlight, rows, all, (r) => r.path, () => {
    setView("all");
    setSearch("");
    setReadOnly(false);
  });

  const newest = snap.drops.reduce<(typeof snap.drops)[number] | undefined>(
    (best, d) => (!best || d.id > best.id ? d : best),
    undefined,
  );

  return (
    <>
      <SheetHeader
        title="Portal files"
        onExport={() => downloadCsv("portal-files.csv", FILE_COLUMNS, rows)}
        exportDisabled={rows.length === 0}
        views={FILE_VIEWS}
        view={view}
        onView={setView}
        search={search}
        onSearch={setSearch}
        searchLabel="Search files"
        sortLabel={oldestFirst ? "Oldest drop first" : "Newest drop first"}
        sortFlipped={oldestFirst}
        onSort={() => setOldestFirst((o) => !o)}
        filterLabel={readOnly ? "Showing files Ingest read rows from · show all" : "Only files Ingest read rows from"}
        filterOn={readOnly}
        onFilter={() => setReadOnly((f) => !f)}
      />
      <SheetTable
        rows={rows}
        columns={FILE_COLUMNS}
        rowId={(r) => r.path}
        highlight={highlight.ids}
        highlightSeq={highlight.seq}
        scanning={scanning}
        caption="Supplier-portal files per data drop and the rows Ingest read from each"
        empty={all.length ? "No files match this view." : "No portal drops yet. Play the next data drop."}
      />
      <SheetFooter>
        {fmt(rows.length)} of {fmt(all.length)} files · {fmt(snap.drops.length)} drops
        {newest ? ` · last check ${clock(newest.checked_at)}` : ""}
      </SheetFooter>
    </>
  );
}

// ---------------------------------------------------------------------------
// Small pieces
// ---------------------------------------------------------------------------

/** When an answer points at rows hidden by the current view, fall back to the full list so they show. */
function useRevealHighlight<T>(
  highlight: Highlight,
  visible: T[],
  all: T[],
  idOf: (row: T) => string,
  reset: () => void,
) {
  const [handled, setHandled] = useState(0);
  if (highlight.seq !== handled) {
    setHandled(highlight.seq);
    const shown = new Set(visible.map(idOf));
    const known = new Set(all.map(idOf));
    if ([...highlight.ids].some((id) => known.has(id) && !shown.has(id))) reset();
  }
}

function SheetFooter({ children }: { children: ReactNode }) {
  return (
    <p className="border-t border-border/70 px-6 py-2.5 text-xs text-muted-foreground tabular-nums sm:px-8">{children}</p>
  );
}

function SheetError({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div className="mx-6 mt-4 rounded-xl border border-signal-red/25 bg-signal-red-bg px-4 py-3 text-sm text-signal-red sm:mx-8">
      Couldn&apos;t load the truth store: {message}.{" "}
      <button type="button" onClick={onRetry} className="font-semibold underline underline-offset-2">
        Try again
      </button>
    </div>
  );
}
