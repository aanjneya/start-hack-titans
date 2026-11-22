"use client";

import { useState } from "react";
import type { DecisionCard, DecisionSource, Evidence } from "../../API_CONTRACT";
import { getData } from "@/lib/api";

export function sameCitation(a: Evidence, b: Evidence) {
  return a.path === b.path && a.locator === b.locator && a.quote === b.quote && a.sha256 === b.sha256;
}

export function SourcePassage({ decision, index, evidence, label }: {
  decision: number; index: number; evidence: Evidence; label: string;
}) {
  const [record, setRecord] = useState<DecisionSource | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const read = async () => {
    if (index < 0 || loading || record) return;
    setLoading(true);
    setError("");
    try { setRecord(await getData<DecisionSource>(`/api/decisions/${decision}/evidence/${index}`)); }
    catch (e) { setError((e as Error).message); }
    finally { setLoading(false); }
  };
  return <details className="my-2 text-sm" onToggle={e => { if (e.currentTarget.open) void read(); }}>
    <summary className="cursor-pointer font-semibold text-act">{label}</summary>
    <div className="mt-2 border-l-2 border-rule pl-3">
      <p className="break-words font-medium">{record?.title || evidence.path.split("/").pop()}</p>
      <p className="text-xs text-muted">{evidence.source_kind || "Decision evidence"} · {evidence.locator}
        {record?.published && ` · Published ${record.published}`}</p>
      {evidence.quote && <blockquote className="my-2 whitespace-pre-line break-words">{evidence.quote}</blockquote>}
      {loading && <p role="status">Loading the source passage…</p>}
      {error && <p role="alert">{error} <button className="text-act underline" onClick={read}>Retry</button></p>}
      {record?.excerpt && record.excerpt !== evidence.quote && <details className="mt-2">
        <summary className="cursor-pointer text-act">Surrounding document text</summary>
        <p className="mt-1 break-words text-muted">{record.excerpt}</p></details>}
      {!evidence.quote && !loading && !error && <p>The decision records this source and location.</p>}
      <p className="mt-2 break-all text-xs text-muted">{evidence.path}</p>
    </div>
  </details>;
}

export function SourceList({ evidence, cards }: { evidence: Evidence[]; cards: DecisionCard[] }) {
  const [open, setOpen] = useState(false);
  const groups = new Map<string, Evidence[]>();
  for (const item of evidence) {
    const current = groups.get(item.path) ?? [];
    if (!current.some(e => sameCitation(e, item))) current.push(item);
    groups.set(item.path, current);
  }
  return <details className="my-2" onToggle={e => setOpen(e.currentTarget.open)}>
    <summary className="cursor-pointer">Source evidence ({groups.size} sources)</summary>
    {open && <ul className="mt-3 divide-y divide-rule">{Array.from(groups, ([path, items]) => <li className="py-2" key={path}>
      <p className="break-words font-medium">{path.split("/").pop() || path}</p>
      {items.map((e, i) => {
        if (/^https?:\/\//i.test(path)) return <p key={i} className="my-1 break-words">{e.quote}{" "}
          <a className="text-act underline" href={path} target="_blank" rel="noreferrer">Open cited source ↗</a></p>;
        const card = cards.find(c => c.evidence.some(x => sameCitation(x, e)));
        return <SourcePassage key={i} decision={card?.id ?? 0} index={card?.evidence.findIndex(x => sameCitation(x, e)) ?? -1}
          evidence={e} label={e.locator || e.source_kind || "Read citation"} />;
      })}
    </li>)}</ul>}
  </details>;
}
