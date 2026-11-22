"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import type { BuyerActivity, BuyerQueueEntry, CustomerSetup, DecisionCard, InboxMessage } from "../../API_CONTRACT";
import { API_URL, clock } from "@/lib/api";

const levels = [
  {id:"mine", label:"My approvals", detail:"Actions that need your signature."},
  {id:"outcomes", label:"Decisions & outcomes", detail:"Also show routed decisions and supplier replies."},
  {id:"activity", label:"Agent activity", detail:"Also show checks and handoffs from the six agents."},
] as const;
type Level = typeof levels[number]["id"];
type Update = {id:number; at:string; text:string; key?:string; mail?:number; tier:Level};
type Props = {setup:CustomerSetup; activity:BuyerActivity[]; queue:BuyerQueueEntry[]; inbox:InboxMessage[]; decisions:DecisionCard[];
  onAction:(key:string)=>void; onMail:(id:number)=>void; describeReply:(m:InboxMessage)=>string};

const activityLabels: Record<string,string> = {
  check:"Watcher checked the supplier portal", replay_manifest:"Watcher passed the historical file changes to Ingest",
  drop_ingested:"Ingest processed customer records",
  reconciled:"Reconciler checked the latest evidence", risk_rated:"Risk Rater checked supplier and product grades",
  forecast:"Planner recalculated the packing forecast", handoff:"Planner handed a request to Chaser",
  mock_send:"Chaser recorded a simulated supplier email", send_email:"Chaser sent a supplier email",
  draft_email:"Chaser prepared a supplier email for delivery",
};

function updates({activity, queue, inbox, decisions, setup, describeReply}:Omit<Props,"onAction"|"onMail">): Update[] {
  const recommended = new Map(decisions.map(c => [c.id, c.recommended]));
  const byCard = new Map<number, BuyerQueueEntry>();
  for (const entry of queue) for (const source of entry.sources) {
    if (!source.related_review && (!byCard.has(source.decision_id) || source.option_index === recommended.get(source.decision_id)))
      byCard.set(source.decision_id, entry);
  }
  const byReply = new Map(inbox.map(r => [r.id, r]));
  const seen = new Set<string>();
  const items: Update[] = [];
  for (const row of activity) {
    let item: Omit<Update, "id"|"at"> | undefined;
    let identity = row.run_id + row.agent + row.action;
    const entry = byCard.get(Number(row.data.decision_id));
    const reply = byReply.get(Number(row.data.inbox_id));
    if (row.action === "decision_created" && entry) {
      const owners = entry.option.assignments?.map(a => a.person).filter(Boolean) ?? [];
      const mine = !!setup.demo_buyer && owners.includes(setup.demo_buyer);
      const change = entry.latest_grade_change;
      const grade = change && change.run_id === row.run_id ? `${change.supplier} ${change.previous} → ${change.current}. ` : "";
      item = {tier:mine ? "mine" : "outcomes", key:entry.key,
        text:grade + (mine ? "Your approval · " : `Awaiting ${owners.join(" + ") || "assignment"} · `) + entry.option.label};
      identity = "action/" + entry.key;
    } else if (row.agent === "ingest" && reply?.processed && reply.outbox_id) {
      const measured = reply.forecast_at_confirmation?.filter(f => f.change_from_previous != null && f.change_from_previous !== 0) ?? [];
      const outcome = measured.map(f => `${f.edition} ${f.change_from_previous! > 0 ? "+" : ""}${f.change_from_previous!.toLocaleString("en-GB")} by close`).join(", ");
      item = {tier:"outcomes", mail:reply.outbox_id, text:outcome || describeReply(reply)};
      identity = "reply/" + reply.id;
    } else if (row.action === "review_withdrawn") {
      item = {tier:"outcomes", text:row.detail};
      identity = "withdrawn/" + row.data.decision_id;
    } else if (activityLabels[row.action]) {
      item = {tier:"activity", text:activityLabels[row.action]};
    }
    if (item && !seen.has(identity)) {
      seen.add(identity);
      items.push({...item, id:row.id, at:row.ts});
    }
  }
  return items;
}

function stored(key:string) {
  try {
    const parsed = JSON.parse(localStorage.getItem(key) || "null");
    if (parsed && levels.some(l => l.id === parsed.level))
      return {level:parsed.level as Level, seen:typeof parsed.seen === "number" ? parsed.seen : null};
  } catch { /* Private browsing can disable local storage. */ }
  return {level:"outcomes" as Level, seen:null};
}

export default function BuyerUpdates(props:Props) {
  const storeKey = JSON.stringify(["buyer-updates", API_URL, props.setup.customer, props.setup.site, props.setup.demo_buyer]);
  const [pref, setPref] = useState(() => stored(storeKey));
  const [open, setOpen] = useState(false);
  const panel = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const outside = (e:PointerEvent) => { if (!panel.current?.contains(e.target as Node)) setOpen(false); };
    document.addEventListener("pointerdown", outside);
    return () => document.removeEventListener("pointerdown", outside);
  }, [open]);
  const {activity, queue, inbox, decisions, setup, describeReply} = props;
  const all = useMemo(() => updates({activity, queue, inbox, decisions, setup, describeReply}),
    [activity, queue, inbox, decisions, setup, describeReply]);
  const visible = all.filter(item => item.tier === "mine" || pref.level === "activity" || (pref.level === "outcomes" && item.tier === "outcomes"));
  const newest = activity[0]?.id;
  useEffect(() => {
    if (newest != null && (pref.seen === null || open || pref.seen > newest) && pref.seen !== newest) {
      setPref(p => ({...p, seen:newest}));
    }
  }, [newest, open, pref.seen]);
  useEffect(() => {
    try { localStorage.setItem(storeKey, JSON.stringify(pref)); } catch { /* Preferences still apply in this tab. */ }
  }, [pref, storeKey]);
  const unread = pref.seen == null ? 0 : visible.filter(item => item.id > pref.seen!).length;
  return <div className="relative" ref={panel}>
    <button className="rounded-md border border-rule bg-surface px-3 py-2 text-sm font-semibold hover:border-act"
      aria-expanded={open} aria-controls="buyer-update-list" onClick={() => setOpen(!open)}>My updates
      <span aria-live="polite">{unread > 0 && ` · ${unread} new`}</span></button>
    {open && <section id="buyer-update-list" aria-label="My updates"
      className="absolute top-full z-20 mt-2 w-[min(23rem,calc(100vw-2rem))] rounded-lg border border-rule bg-surface p-4 text-sm shadow-lg max-sm:fixed max-sm:inset-x-4 max-sm:top-auto sm:right-0"
      onKeyDown={e => { if (e.key === "Escape") { setOpen(false); document.querySelector<HTMLButtonElement>('[aria-controls="buyer-update-list"]')?.focus(); } }}>
      <fieldset><legend className="font-bold">Keep me updated about</legend>
        <p className="mb-2 text-xs text-muted">On this device · {props.setup.demo_buyer}</p>
        {levels.map(level => <label key={level.id} className="flex cursor-pointer items-start gap-2 py-1">
          <input name="buyer-updates-level" type="radio" className="mt-1 accent-act" checked={pref.level === level.id}
            onChange={() => setPref(p => ({...p, level:level.id}))} />
          <span><strong>{level.label}</strong><span className="block text-xs text-muted">{level.detail}</span></span></label>)}
      </fieldset>
      <h3 className="mt-3 border-t border-rule pt-3 font-bold">Recent updates</h3>
      {!visible.length ? <p className="mt-1 text-muted">No matching updates in recent activity.</p> :
        <ul className="mt-1 max-h-56 overflow-y-auto">{visible.slice(0, 8).map(item => <li key={item.id} className="border-b border-rule py-2 last:border-b-0">
          <time className="block text-xs text-muted">{clock(item.at)}</time>
          {item.key || item.mail ? <button className="text-left font-medium text-act underline" onClick={() => {
            setOpen(false); if (item.key) props.onAction(item.key); else if (item.mail) props.onMail(item.mail);
          }}>{item.text}</button> : <a className="text-act underline" href={API_URL + "/api/trace?trace_id=" + item.id}>{item.text}</a>}
        </li>)}</ul>}
      <button className="mt-2 font-semibold text-act" onClick={() => setOpen(false)}>Close updates</button>
    </section>}
  </div>;
}
