"""Read the current truth store and its source locators. Safe for historical drop replay."""
from __future__ import annotations

import calendar
import html
import re
from collections import Counter, defaultdict
from datetime import timedelta

from ..core import models as m
from ..core.parsing import parse_datetime
from ..core.store import all_rows
from .planner_forecast import Plan


def evidence(path, locator, quote=""):
    return {"path": path.partition("#")[0], "locator": locator, "quote": quote, "sha256": ""}


def clock(text, anchor):
    day = re.search(r"\b(" + "|".join(calendar.day_name) + r")\s+(\d{2}:\d{2})", text, re.I)
    if day:
        weekday = list(calendar.day_name).index(day[1].title())
        date = anchor - timedelta(days=(anchor.weekday() - weekday) % 7)
        return parse_datetime(f"{date}T{day[2]}")
    iso = re.search(r"\b(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2})", text)
    return parse_datetime(iso[1]) if iso else None


def load_plan():
    editions = list(all_rows(m.LaunchStatus))
    products = {r.product_id: r for r in all_rows(m.Product)}
    dates = {products[r.product_id].launch_date for r in editions if r.product_id in products}
    if len(dates) != 1 or None in dates or not editions:
        return None
    launch_day = dates.pop()
    documents = list(all_rows(m.Document))
    notes = [d for d in documents if d.kind == "note"]
    history = list(all_rows(m.FieldChange))
    source_sheets = {r.source for r in editions}
    source_sheets.update(c.source for c in history if c.table_name == "launch_status")
    snapshots = [(clock(d.text, launch_day), d) for d in notes
                 if d.path.removesuffix("#notes") in source_sheets]
    snapshots = [(dt, d) for dt, d in snapshots if dt]
    shifts = []
    ev = []
    for doc in documents:
        for row in doc.extracted.get("rows", []):
            if row.get("Day") not in calendar.day_name:
                continue
            times = re.findall(r"\d{2}:\d{2}", html.unescape(row.get("Shift", "")))
            if len(times) != 2:
                continue
            day = launch_day - timedelta(days=(launch_day.weekday() - list(calendar.day_name).index(row["Day"])) % 7)
            shifts.append(tuple(parse_datetime(f"{day}T{t}") for t in times))
            ev.append(evidence(doc.path, row["Day"], html.unescape(row["Shift"])))
    if not shifts or not snapshots:
        return None
    as_of, status_doc = max(snapshots, key=lambda x: x[0])
    shifts = sorted(set(shifts))
    deadline = max(end for _, end in shifts)
    # A separately stated launch cutoff can be earlier than the scheduled shift end.
    for doc in documents:
        if doc.kind in {"context", "memo"}:
            match = re.search(r"(?:on sale|retail).*?\b" + launch_day.strftime("%A") +
                              r"(?:\s+\d{1,2}\s+\w+\s+\d{4}\s+at)?\s+(\d{2}:\d{2})", doc.text, re.I)
            if match:
                deadline = min(deadline, parse_datetime(f"{launch_day}T{match[1]}"))
                ev.append(evidence(doc.path, "launch deadline", match[0]))
    ev.append(evidence(status_doc.path, "edition rows", status_doc.text.splitlines()[0]))
    priority_text = " ".join(d.text.lower() for d in documents if d.kind in {"memo", "note"})
    def priority(row):
        name = re.escape(row.edition.casefold().replace("’", "'"))
        return 0 if re.search(name + r"(?: edition| pre-orders)? first", priority_text) else 1
    editions.sort(key=lambda r: (priority(r), r.product_id))
    bom = defaultdict(dict)
    for row in all_rows(m.BomLine):
        if (not row.valid_from or row.valid_from <= as_of.date()) and (not row.valid_to or row.valid_to >= as_of.date()):
            bom[row.parent_id][row.child_id] = row.qty_per
            if row.parent_id in {e.product_id for e in editions}:
                ev.append(evidence(row.source, f"{row.parent_id}/{row.child_id}"))
    for part in all_rows(m.SupplierPartMap):
        texts = [part.note] + [c.old_value or "" for c in history if c.table_name == "supplier_part_map"
                                and c.row_id == part.id and c.field == "note"]
        for note in texts:
            fitted = re.search(r"fitted by supplier into ([\w-]+)", note, re.I)
            if fitted:
                for parts in bom.values():
                    if fitted[1] in parts and part.component_id in parts:
                        parts.pop(part.component_id)
                        ev.append(evidence(part.source, part.component_id, note))
    plan = Plan(as_of, deadline, shifts, editions, dict(bom), Counter(), evidence=ev)
    changes = {}
    for row in sorted(all_rows(m.EngineeringChange), key=lambda c: (c.effective_from or as_of.date(), c.id)):
        if row.effective_from and row.effective_from <= as_of.date():
            changes[row.child_id] = row
            ev.append(evidence(row.source, row.change_id, row.reason))
    receipts = list(all_rows(m.GoodsReceipt))
    by_batch = {r.gr_number: r for r in receipts}
    confirmations = defaultdict(list)
    for row in all_rows(m.OrderConfirmation):
        confirmations[(row.supplier_id, row.po_number, row.po_line, row.material_id)].append(row)
    lots = list(all_rows(m.StockLot))
    sites = {r.site for r in lots}
    site = next(iter(sites)) if len(sites) == 1 else None
    for d in documents:
        match = re.search(r"packed at [^\n]*?\(([\w-]+)\)", d.text, re.I) if d.kind == "context" else None
        if match:
            site = match[1]
            break
    lots = [r for r in lots if r.site == site and r.snapshot_date and r.snapshot_date <= as_of.date()]
    dates_by_part = {r.material_id: r.snapshot_date for r in sorted(lots, key=lambda r: r.snapshot_date)}
    inspections = [d for d in documents if d.kind == "inspection" and re.search(r"blocking requested|rejected", d.text, re.I)]
    # Receipts after the stock snapshot are new physical lots. Same-day receipts lack a time;
    # show them as uncertain until a dated count or a timed receiving record can establish availability.
    for receipt in receipts:
        snap = dates_by_part.get(receipt.material_id)
        if (receipt.plant == site and snap and receipt.gr_date and snap < receipt.gr_date < as_of.date()
                and receipt.qty_received and not any(l.batch == receipt.gr_number for l in lots)):
            lots.append(m.StockLot(snapshot_date=snap, site=site, material_id=receipt.material_id,
                                   batch=receipt.gr_number, quantity=receipt.qty_received, source=receipt.source))
    for lot in lots:
        part = lot.material_id
        if dates_by_part[part] != lot.snapshot_date:
            continue
        receipt = by_batch.get(lot.batch)
        matches = confirmations.get((receipt.supplier_id, receipt.po_number, receipt.po_line, part), []) if receipt else []
        revision = lot.revision or (matches[0].revision if len(matches) == 1 else "")
        change = changes.get(part)
        reason = ""
        proof = [evidence(lot.source, f"{site}/{lot.batch}")]
        if receipt:
            proof.append(evidence(receipt.source, f"{receipt.gr_number}, PO {receipt.po_number}/{receipt.po_line}"))
        if len(matches) == 1:
            proof.append(evidence(matches[0].source, f"page 1, PO line {matches[0].po_line}", revision))
        if change and not change.use_up_old_rev and revision != change.rev_new:
            reason = "revision blocked" if revision else "revision unverified"
        for doc in inspections:
            if lot.batch and lot.batch in doc.text:
                reason = "Quality hold"
                proof.append(evidence(doc.path, f"page 1, receipt {lot.batch}"))
        if not reason:
            plan.stock[part] += lot.quantity or 0
        plan.lots.append({"part": part, "batch": lot.batch, "revision": revision, "quantity": lot.quantity,
                          "status": reason or "eligible", "evidence": proof})
    plan.assumptions.append("Date-only inbound is pending until a timed site delivery and a released dock slot "
                            "can be matched to the shipment and revision.")
    plan.assumptions.append("Stock is eligible unless revision evidence or a Quality hold excludes it. "
                            "When pack-out does not identify a lot, deduct consumption from eligible units first.")
    packout = list(all_rows(m.Packout))
    for row in editions:
        old_counts = [c for c in history if c.table_name == "launch_status" and c.row_id == row.id
                      and c.field == "packed" and c.old_value is not None]
        oldest = min(old_counts, key=lambda c: c.id, default=None)
        for part, per in bom.get(row.product_id, {}).items():
            snap = dates_by_part.get(part)
            if snap is None:
                continue
            logs = [p for p in packout if p.site == site and p.product_id == row.product_id
                    and p.pack_date and snap < p.pack_date < as_of.date()]
            used = sum(p.units_packed or 0 for p in logs)
            proof = [evidence(p.source, f"{p.pack_date} {p.product_id}") for p in logs]
            previous = next((dt for dt, d in snapshots if d.path.removesuffix("#notes") == row.source), None)
            if oldest and row.packed is not None and previous and previous.date() == snap:
                used = max(used, row.packed - int(float(oldest.old_value)))
                proof.extend([evidence(row.source, row.product_id, f"Packed at stock snapshot: {oldest.old_value}"),
                              evidence(status_doc.path, row.product_id, f"Packed at launch update: {row.packed}")])
            if used:
                plan.consumption.append({"part": part, "product": row.product_id, "quantity": used * per,
                                         "evidence": proof})
            plan.stock[part] = max(0, plan.stock[part] - used * per)
    _inbound(plan, site, confirmations, receipts, changes, documents)
    return plan


def _inbound(plan, site, confirmations, receipts, changes, documents):
    orders = defaultdict(set)
    materials = defaultdict(set)
    for link in all_rows(m.ShipmentPO):
        orders[link.shipment_id].add(link.po_number)
    for link in all_rows(m.ShipmentMaterial):
        materials[link.shipment_id].add(link.material_id)
    # A site-controlled release table is a separate input from the supplier's promise.
    slots = {}
    for doc in documents:
        if doc.path.startswith("suppliers/"):
            continue
        for row in doc.extracted.get("rows", []):
            if row.get("receiving_site") == site and row.get("dock_released_at"):
                slots[(row.get("delivery_reference"), row.get("approved_revision", ""))] = (
                    parse_datetime(row["dock_released_at"]), evidence(doc.path, row.get("delivery_reference", "")))
    advice = {}
    for msg in sorted(all_rows(m.InboxMessage), key=lambda r: r.id):
        fact = (msg.result or {}).get("confirmed_delivery")
        if msg.processed and fact:
            advice[(msg.supplier_id, fact["po_number"], fact["po_line"], fact["shipment_id"])] = (fact, msg.id)
    visited = set()
    for ship in sorted(all_rows(m.Shipment), key=lambda s: (s.id or 0)):
        if ship.shipment_id in visited or ship.dest_site != site:
            continue
        visited.add(ship.shipment_id)
        for (supplier, po, line, part), rows in confirmations.items():
            if supplier != ship.supplier_id or po not in orders[ship.shipment_id] or part not in materials[ship.shipment_id]:
                continue
            if len(rows) != 1:
                continue
            oc = rows[0]
            received = sum(r.qty_received or 0 for r in receipts
                           if (r.supplier_id, r.po_number, r.po_line, r.material_id) == (supplier, po, line, part))
            outstanding = max(0, (oc.quantity or 0) - received)
            if not outstanding:
                continue
            key = (supplier, po, line, ship.shipment_id)
            fact, inbox_id = advice.get(key, ({}, None))
            rev = fact.get("revision", oc.revision)
            qty = min(outstanding, fact.get("quantity", outstanding))
            slot, dock_proof = slots.get((ship.shipment_id, rev), (None, None))
            eta = parse_datetime(fact.get("arrival"))
            proof = [evidence(ship.source, ship.shipment_id), evidence(oc.source, f"page 1, PO {po}/{line}")]
            if inbox_id:
                proof.append(evidence(f"inbox/{inbox_id}", f"confirmed delivery {po}/{line}"))
            if dock_proof:
                proof.append(dock_proof)
            change = changes.get(part)
            blocked = bool(change and not change.use_up_old_rev and rev != change.rev_new)
            if slot and eta and not blocked:
                # A PO line shared by multiple shipments never contributes more than the unreceived balance.
                prior = sum(v[2] for k, v in plan.arrivals.items() if k[:3] == key[:3])
                plan.arrivals[key] = (max(slot, eta), part, max(0, min(qty, outstanding - prior)))
            plan.inbound.append({"part": part, "supplier_id": supplier, "po_number": po, "po_line": line,
                                 "shipment_id": ship.shipment_id, "revision": rev, "quantity": qty,
                                 "eta": fact.get("arrival") or (str(ship.eta) if ship.eta else None),
                                 "dock_released": bool(slot), "dock_at": slot.isoformat() if slot else None, "eligible": key in plan.arrivals,
                                 "revision_blocked": blocked, "evidence": proof})
