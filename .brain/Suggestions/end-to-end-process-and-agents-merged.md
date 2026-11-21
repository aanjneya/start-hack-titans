# End-to-End Process & Agents: MERGED plan (aanjneya + daksh)

> Combines `end-to-end-process-and-agents-aanjneya.md` (7-step story, specific data traps, pack simulation, one screen + trace drawer) with `-daksh.md` (closed loop, live drop watcher, explicit hand-offs and human gates, build order, verification). Facts below were spot-checked against the portal data at 17:10.

## 1. The story (pitch spine, from aanjneya)
Soony has a system of record for only 2 of 7 steps. We close the other five.
```
[01 ORDER] → [02 CONFIRM] → [03 SHIP] → [04 TRACK] → [05 RECEIVE] → [06 PACK] → [07 LAUNCH Sun 20:00]
   SAP ✓       PDF/email ✗     carriers ✗   Excel ✗      dock scans ✗   pack log ✓
```
**One loop (from daksh):** new drop or email → normalize into one truth → check against what the launch needs → chase what's missing → Plan A/B/C with **€ cost vs copies gained** → human approves in one click → agent executes → **next drop or reply confirms it, or the issue reopens.**

## 2. Agents (5 + coordinator; one verb each, so not "one big prompt")
| # | Agent | Job | Key inputs | Hands off to | Human gate |
|---|---|---|---|---|---|
| 0 | **Watcher** (built: `monitor/drop_watch.py`) | Detect new/changed/removed portal files every 15 min via SHA-256 | `/api/v1/files` | 1 | none |
| 1 | **Ingest & Normalizer** | Schema-resilient parsing (CSV `;`/`,`, legacy `EBELN/MENGE` POs, XLSX, PDF/DOCX, KR/CN/PL/EN) → canonical tables (PO, Confirmation, Shipment, Receipt, Stock, BOM/ECN, Notice) with **provenance on every field** | all files | 2 | none |
| 2 | **Reconciler & Gate** | Conflicts & traps: ERP vs tracker (SHP-04276 `arrived_port` vs `in transit`; 11 rush shipments missing from tracker), stale `updated_at`, PO vs confirmation, **ECN revision gate** (CMP-GAM-004 rev B must not be packed), Quality release, IBAN vs contract, qty jumps, POs stuck `awaiting_approval` (SUP-0018 CMP-GAM-010) | canonical store | 3 if info missing, 4 if copies at risk | IBAN changes + qty jumps always to a human |
| 3 | **Chaser** | Multilingual, **rate-limited** supplier/carrier emails (suppliers stop answering if spammed); parses replies back into 1 | issues | 1 (reply), escalates on silence | first email per supplier previewed; `MOCK_MODE` supplier simulator |
| 4 | **Launch Planner & Decision Copilot** | Hour-by-hour pack simulation (3 Std lines 6,000/h, CE cell 900/h, Sat 06–22 / Sun 06–20), **Collector's first** (CPO memo), Plan A/B/C (last-mile pickup at CTA, air, Karton Nowak express, reallocate parts), decision card routed to the **valid PP-07 approver** (org_chart_v2: Arjun Krishnan starts 23 Nov → route to Maren/Jonas) | issues, risk, stock, inbound | human → executes via 3 / records | budget/policy actions need one-click approval |
| 5 | **Risk Rater** ("Moody's", AAA–CCC = 1–6) | Explainable score per part & supplier: single source (memo asks for 2nd sources), hours-to-line-stop buffer, supplier health (downtime, expired release), transit/news risk; top 3 reasons shown | store + history | 2 and 4 | none |
| – | **Coordinator** (code) | Routes hand-offs, holds state, writes every read/decision/action + evidence to the **Trace log** | all events | UI drawer | none |

Merge decisions: kept Risk separate from Line-Stop/Policy (daksh) so each agent stays narrow; folded policy + quality checks into the Reconciler's gate (aanjneya's Stage 5 gate) and approver routing into the Planner.

## 3. One screen (aanjneya) + trace drawer
- **Top bar:** countdown to Sun 20:00, 7-step pipeline status, `LIVE / MOCK` toggle.
- **Main:** "What can stop a line" brief (CPO memo: one page, not 40 dashboards) → decision cards (problem, A/B/C, **€ vs copies**, approver, decide-by time, Approve). **Approving updates the pack forecast live.**
- **Risk table** with reasons. **⚡ AI Traceability drawer** on any card: source file → normalization diff → reasoning → email out/in (for Nils and the judges).

## 4. Hero demo case (real data, verified)
**CMP-GAM-004 cover sleeve (Standard):** ECN-2026-118 → rev B blocked → Reconciler invalidates rev B stock → Planner shows the shortfall in copies → Karton Nowak (SUP-0028, 60 % share) has a **downtime notice** (`Zawiadomienie_przestoj_techniczny_2026-11.pdf`) **and** an **express offer** (`Oferta_tryb_ekspresowy_2026-11-21.pdf`) → Chaser asks in Polish if express output is rev C → card: "Express rev C, € X, +Y copies; approver Maren" → click → forecast turns green → trace shows the chain.
Backups: SHP-04276 waiting at CTA Hamburg (last-mile pickup = cheap copies); CMP-GAM-010 flagged critical with sea ETAs in Dec and SUP-0018 POs stuck `awaiting_approval`.

## 5. Build order (Sat 17:30 → Sun 12:00)
| P | Slice | Owner (proposal) |
|---|---|---|
| P0 | Canonical store + Ingest/Normalizer for ERP CSVs, both PO formats, tracker xlsx, BOM/ECN, `launch_status` (reuse `office_text`/`csv_summary` from `monitor/drop_watch.py`) | tbd |
| P0 | Reconciler rules: tracker vs ERP, stale, ECN rev gate, PO vs confirmation, stuck approvals | tbd |
| P0 | Planner: pack simulation + A/B/C + € vs copies + PP-07 approver routing | tbd |
| P0 | One screen + trace drawer, MOCK/LIVE toggle, fixtures from `challenge/start-hack-data/` | tbd |
| P1 | Chaser with rate limit + mock supplier, then one real email to Karton Nowak | first free |
| P1 | Risk Rater (4 explainable factors) | — |
| P2 | Dock-scan OCR for receiving (do 2–3 scans for the demo, not all 61); supplier scout for 2nd sources | pitch slide |
Tag `demo-safe` Sun 11:00, record 90 s backup video.

## 6. Verification
- Replay baseline (14:15) then the 16:08 drop from `challenge/start-hack-data/versions/` → system raises the CMP-GAM-004 rev issue and CMP-GAM-010 issue **with no code change**.
- Schema test: old vs new `products.csv` (`plan_weekly_units` → `weekly_plan_qty`) parses identically.
- Click test: issue → card → approve → email logged → forecast changes → trace has evidence links.
- Run with `MOCK_MODE=true` and Wi-Fi off.

## 7. Open decisions for the team
1. Owners for the four P0 slices. 2. Stack (fill `API_CONTRACT.ts` first). 3. Then fill `.brain/GOAL.md` from §1 and §4 (needs explicit approval).
