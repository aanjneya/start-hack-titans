# End-to-End Procurement & Supply Chain Loop — Proposal (`aanjneya`)

> Synthesizing [plan discussion 16 uhr.txt](../minutes/plan%20discussion%2016%20uhr.txt), the **16:08 live data drop** (`memo_launch_priorities.docx`, `launch_status.xlsx`, `bom_delta_2026-11-21.csv`, `org_chart_v2.pdf`, `products.csv`), and Lio's judging rubric.

---

## 1. Core Thesis: Close All 7 Steps From Start to End

Soony only has a system of record for **2 of 7 steps** (`01 Order` in SAP and `06 Pack` in the pack-out log). Everything in between (`Confirm`, `Ship`, `Track`, `Receive`, `Launch`) lives in multilingual PDFs, Excel trackers, dock scans, and phone calls.

```
[01 ORDER] ──► [02 CONFIRM] ──► [03 SHIP] ──► [04 TRACK] ──► [05 RECEIVE] ──► [06 PACK] ──► [07 LAUNCH]
   (SAP)       (PDF/Email)     (4 Carriers)    (Excel)      (Dock Scans)    (Pack Log)     (Sun 20:00)
  ✓ System       ✗ Messy         ✗ Messy       ✗ Messy        ✗ Messy        ✓ System
```

To build a true **Start-to-End** product (and a startup that scales beyond Soony), our platform connects all 7 steps into one **closed-loop pipeline**: from **Demand & BOM** all the way to **Parts Landed at the Hamburg Dock & Packed into Boxes**.

---

## 2. The 6-Stage End-to-End Closed Loop

### Stage 1: Demand & BOM Planning (The "Start")
- **Inputs**: `products.csv` (schema-resilient: handles `plan_weekly_units` → `weekly_plan_qty`), `launch_status.xlsx`, `bom.csv`, and live ECNs (`bom_delta_2026-11-21.csv`).
- **Behavior**: Calculates live net component demand for DC-HAM packing lines (`86,000` Standard @ `6,000/hr` + `18,900` Collector's @ `900/hr`, enforcing CPO Jonas Lindqvist's **"Collector's Edition first"** priority rule).
- **Live Trigger**: When `ECN-2026-118` upgrades `CMP-GAM-004` (Standard Cover Sleeve) from Rev B → Rev C and forbids packing Rev B, the system immediately invalidates Rev B stock and recalculates net shortage.

### Stage 2: Sourcing, Ordering & Confirmation (Steps 01 & 02)
- **Inputs**: SAP POs (`purchase_orders_2025H2-2026.csv`) vs. Supplier Order Confirmations (PDFs in `ko`, `zh`, `pl`, `en`), Contracts, Price Lists, and Notices.
- **Behavior**: Normalizes all unstructured supplier documents into a **Canonical SAP Source of Truth** (unified IDs, currencies, dates, revisions, full provenance links).
- **3-Way Match & Governance Check**: Flags POs stuck in `awaiting_approval` (e.g., `SUP-0018` Pune Diecast Works after its 90-day conditional release expired on Oct 30) and checks effective dates on `org_chart_v2.pdf` (catching that `Arjun Krishnan` starts `2026-11-23` — *after* launch — so approvals must route to Dr. Maren Vogt / Jonas Lindqvist per PP-07).

### Stage 3: In-Transit Tracking & Live Interception (Steps 03 & 04)
- **Inputs**: `open_shipments.csv`, `open_shipments_tracker_v37_FINAL_final2.xlsx`, `routes.csv`, `ports.csv`, `logistics_providers.csv`.
- **Behavior**: Tracks live GPS (`last_lat`, `last_lon`), carrier, mode, and `ETA` against the exact **Hours-to-Line-Stop** clock at DC-HAM.
- **Catches In-Flight Failures**:
  - `SHP-2026-04276` (`CMP-GAM-003` Clear Cases) sitting `arrived_port` ("discharged at CTA Hamburg, available for pick-up").
  - Late air/sea arrivals (`SHP-2026-04314` arriving Nov 23; `CMP-GAM-010` sea shipments arriving in December).
  - Silent carrier feed freezes (`updated_at` stalled for 18+ hours).

### Stage 4: Autonomous Resolution, Rerouting & Human-in-the-Loop (Closing the Gap)
- **Strategy Generator (Plan A / B / C)**:
  1. **Port / Last-Mile Expedite**: Dispatch local truck (`LSP-04`) to pick up container `NHSU4471921` at CTA Hamburg and book a DC-HAM dock slot (Katrin Jansen / Raj Patel).
  2. **Mode Switch / Air Reroute**: Expedite via Air Cargo (`LSP-02`).
  3. **Emergency Regional Express**: Trigger rush order with Karton Nowak (`SUP-0028`, 9h truck from Poznań) — respecting their Polish downtime notice (`Zawiadomienie_przestoj_techniczny_2026-11.pdf`: Offset B1 down Sat 14:00 → Mon 06:00; Digital B2 capped at 15,000 pcs/36h; finished-goods warehouse stock like 8,000 Art Books still ships in 4h).
  4. **Factory Allocation Shift**: Reallocate shared `CMP-GAM-001` Game Cards to the Collector's cell first.
- **Policy PP-07 Approval Gate**: Packages every budget-impacting action as **"Cost in EUR vs. Copies Gained"** (exact CPO memo format) and routes to the right approver.
- **Execution**: On 1-click approval, dispatches the multilingual supplier/carrier email (`ko`, `zh`, `pl`, `en`), updates the Canonical SAP PO/Shipment record, and books the Hamburg dock arrival.

### Stage 5: Hamburg Dock Receiving & Quality Gate (Step 05)
- **Inputs**: Paper delivery-note scans (`soony/receiving/DC-HAM/*.jpg|*.png`) vs. `goods_receipts.csv` & `stock_snapshot.csv`.
- **Behavior**: OCRs photographed paper delivery notes into SAP goods receipts and enforces the **Quality & Revision Gate** (blocks Rev B `CMP-GAM-004` sleeves and unapproved supplier releases from hitting the packing lines).

### Stage 6: Pack-Out Execution & Launch Readiness (Steps 06 & 07 — The "End")
- **Inputs**: `packout_log.csv`, `launch_status.xlsx`, updated inventory & inbound dock schedule.
- **Behavior**: Runs an hour-by-hour simulation of the 3 Standard lines (`6,000/hr`) and 1 Collector's cell (`900/hr`) until **Sunday 20:00**. Approving a recovery action in Stage 4 immediately updates the live completion forecast on screen.

---

## 3. Agent Architecture (4 Specialized Agents + 1 Canonical SAP Store)

1. **Agent 1 — Ingest & SAP Normalizer**: Schema-resilient parser + vision/OCR for PDFs, dock scans, XLSX, CSVs, and supplier emails → writes versioned records into the Canonical SAP Store with provenance traces.
2. **Agent 2 — Line-Stop, Policy & Risk Engine**: Evaluates BOM + ECN rev rules, packing burn rates, PP-07 approval thresholds, and computes an explainable **Moody's Supply Chain Risk Rating (`AAA`–`CCC` / `1–6`)** per component & supplier grounded in:
   - Single-source dependency (CPO memo explicitly requests post-launch second sources)
   - Hours-to-Line-Stop buffer
   - Supplier compliance & capacity health (expired conditional release, maintenance downtime)
   - Transit & external disruption risk (sea/air delay, stale GPS, news)
3. **Agent 3 — Multilingual Supplier & Carrier Chaser**: Sends targeted, rate-limited emails (`pl`, `ko`, `zh`, `en`) to clarify missing data, verify delays, or trigger rush/expedite options; parses replies back into Canonical SAP.
4. **Agent 4 — Executive Decision Copilot (HITL)**: Generates the CPO's **1-Page Line-Stop Brief** + **1-Click Decision Cards** (*Cost vs. Copies Gained* + valid PP-07 approver).

---

## 4. UI Principle: One Unified Screen + Expandable AI Traceability Drawer

Directly resolves the "1 vs. 2 Dashboards" discussion AND satisfies CPO Jonas Lindqvist's memo (*"One page per day on what can stop a line. Not forty dashboards."*):
- **Top Bar**: Countdown to Sun 20:00 + **7-Step End-to-End Pipeline Status** (`Order → Confirm → Ship → Track → Dock → Pack → Launch`) + `LIVE / MOCK` toggle.
- **Main Canvas**: **1-Page Line-Stop & 1-Click Recovery Cards** (*Cost vs. Copies Gained*) + **Moody's Supplier/Component Risk Table**.
- **Expandable Side Drawer (`⚡ AI Traceability`)**: Click any card or toggle technical mode to inspect the full audit trail — raw source file/PDF → SAP normalization diff → reasoning → outbound supplier email & reply.
