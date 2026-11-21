# 3 · Reconciler & Gate

**Owner:** @aanjneya

## What this agent does
It looks across the clean database and finds what doesn't add up: two sources that disagree, data that stopped updating, a confirmation that doesn't match the order, a part that may no longer be used, suspicious bank details. Every problem becomes an **issue** with proof, and the agent decides who handles it: the Chaser (we need information), the Planner (copies are at risk) or a human (too risky for AI).

## Why we need it
"We find out at the dock" (Maren). This agent finds it first. It's also our answer to the judges' "how do you handle messy and contradictory data?".

## Example from our real data
- Shipment **SHP-2026-04276**: the ERP says "unloaded at CTA Hamburg, ready for pick-up", while the logistics tracker still says "in transit".
- 16:08: engineering change **ECN-2026-118** says Standard sleeve **CMP-GAM-004 rev B must not be packed**, so rev B sleeves in stock or on the way are now useless.

## What it works with
- **Gets:** the database from **Ingest**; risk grades from the **Risk Rater**.
- **Gives:** issues → **Chaser** (missing info), **Launch Planner** (copies at risk), human review (bank details, big quantity jumps). Disagreements are written to the `conflicts` table.

## Tasks (each check = one task)
- [x] **T3.0** Report every check result as a Finding (`.brain/DATA_FORMAT.md`): one-line summary, reason, impact (copies/hours from the data), suggested actions, evidence. — @aanjneya
- [x] **T3.1** ERP shipment list vs logistics tracker: different status or ETA, and shipments missing from the tracker (11 today, incl. all last-minute rush ones). — @aanjneya
- [x] **T3.2** Silent feeds: shipments whose "last updated" stopped moving for many hours (silence is a signal, per the brief). — @aanjneya
- [x] **T3.3** Order vs supplier confirmation: different quantity, date or part revision. — @aanjneya
- [x] **T3.4** Revision gate: block stock and shipments with a revision that a change forbids (ECN-2026-118); allow use-up where permitted (ECN-2026-119). — @aanjneya
- [x] **T3.5** Stuck approvals and expired supplier approval: SUP-0018 Pune Diecast orders "awaiting approval", conditional release ended 30 Oct. — @aanjneya
- [x] **T3.6** Single source + bad news: CMP-MAG-005 lid magnets (4 per Collector's box) have stock 0, no order, one supplier, and a China export ban (17:23 drop). — @aanjneya
- [x] **T3.7** Bank details changed vs the contract → always to a human (possible invoice fraud, a known trap in the brief). — @aanjneya
- [x] **T3.8** Quantity jumps vs history (e.g. 690 → 790) → human check, never accepted automatically. — @aanjneya
- [x] **T3.9** Use the `part_journey` view to find empty steps (ordered but never confirmed or shipped) and route each issue to the right agent. — @aanjneya
- [x] **T3.11** After a supplier reply, `truth.updated` lists the exact rows/fields that changed (`changes`, `issue_ids`): re-check those and resolve the issue if the answer fixed it. Never write supply-chain data yourself (Ingest does). — @aanjneya
- [x] **T3.10** Live data: rerun every check after each drop; close issues the new data solves, reopen ones that come back. — @aanjneya

## When it's done
- On the real drops it raises: the 04276 conflict, the CMP-GAM-004 rev B block, the CMP-GAM-010 / SUP-0018 problem and the CMP-MAG-005 magnet risk, **with no code change between drops**.
- Every issue shows the files that prove it.

---
### Technical notes (for Claude Code, refine later)
- Rules in code (deterministic, testable) plus a Claude pass for fuzzy cases (free-text notes, PDF facts). Thresholds (stale hours, jump %) in config, not hardcoded in logic.
- Issue fields: `type, severity, part_id?, supplier_id?, shipment_id?, po_number?, summary, evidence[{path, locator}], route_to, status`.
- Inputs: `shipments` (ERP + tracker rows, same `shipment_id`), `order_confirmations`, `purchase_orders`, `bom` + ECN rows, `stock`, supplier profiles (`qualification.validUntil`), `news`.
