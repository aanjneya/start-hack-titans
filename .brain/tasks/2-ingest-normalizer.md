# 2 · Ingest & Normalizer

**Owner:** @daksh

## What this agent does
It reads every file Soony and the suppliers send (spreadsheets, Excel, PDFs, files in Polish, Chinese or Korean) and turns them into **one clean database with the same IDs everywhere**. Every value remembers which file it came from. It never deletes old values; it adds new facts next to them.

## Why we need it
"Half of what we know is in a PDF somebody scanned" (onboarding notes). Nobody at Soony reads Polish or Korean. This is the "one source of truth" from our 16:00 discussion, and the judges' "real integration of messy data, nothing hardcoded".

## Example from our real data
There are two order files: the new one uses commas and English column names; the old SAP one uses `;`, German SAP codes (`EBELN`, `MENGE`), US dates and decimal commas. Both must end up in the same `purchase_orders` table. At 16:08 `products.csv` renamed a column (`plan_weekly_units` → `weekly_plan_qty`), and the agent must still read it.

## What it works with
- **Gets:** new or changed files from the **Watcher**; supplier replies from the **Chaser**.
- **Gives:** filled database tables → **Reconciler** and **Risk Rater**; anything it can't map → a "needs checking" note for the Reconciler.

## Tasks
- [ ] **T2.1** Read the ERP spreadsheets in `soony/erp/`: orders (both formats), shipments, goods receipts, stock, pack-out log.
- [ ] **T2.2** Read the master data in `soony/master/`: parts, BOM (parts per box), products, suppliers, routes, ports, carriers, plus the change files (`bom_delta_…`, `components_update_…`).
- [ ] **T2.3** Read the Excel files: the logistics tracker (`soony/logistics/…v37_FINAL_final2.xlsx`) and `soony/context/launch_status.xlsx`.
- [ ] **T2.4** Read each supplier's folder in `suppliers/<ID>_<Name>/`: profile (old CSV and new SAP JSON), parts list, `components_delta.csv`.
- [ ] **T2.5** Read PDFs with AI: order confirmations (`order_confirmations/OA…_<PO>.pdf`), notices (often Polish/Chinese), contracts. Pull out quantity, date, part revision, price and bank details, translated to English.
- [ ] **T2.6** Read the news file (`soony/news/…json`) and memos (`soony/context/*.docx`).
- [ ] **T2.7** Map supplier part and order numbers to our `CMP-…` / `4500…` numbers, with a confidence score.
- [ ] **T2.8** Let AI match column names by meaning, so renamed or new columns still work.
- [ ] **T2.9** Store a source and timestamp on every row; never overwrite.
- [ ] **T2.10** Live data: replaced files add new facts (old ones kept as history); unknown new files, folders or formats are still stored and flagged, never crash the run.

## When it's done
- One run on the real data fills every table, and every row shows its source file.
- The old and new `products.csv` both load correctly.
- Karton Nowak's Polish notices come out as readable English facts (downtime dates, express offer).

---
### Technical notes (for Claude Code, refine later)
- Tables: PLAN.md §3b. Data root: `challenge/start-hack-data/data/` (`soony/…`, `suppliers/…`). Reuse `office_text()` (xlsx/docx) from `monitor/drop_watch.py`; PDFs: `pypdf` text → `claude-sonnet-5-5` structured extraction (tool schema per document type); keep the original text for evidence.
- Legacy PO: `EBELN=po_number, EBELP=po_line, LIFNR=vendor no. (map via profile vendorNo), MATNR=material_id, WERKS=plant, MENGE=quantity, NETPR=unit_price (decimal comma), BEDAT=order_date, EINDT=requested_date, ZZCONF=promised_date, WEDAT=actual_delivery, ZZSTAT=status code`. Dates `MM/DD/YYYY`.
- Shipments: split `po_numbers` and `material_ids` on `|` into link tables. Tracker xlsx uses supplier *names* and `", "`-separated POs.
- Emit `truth.updated{tables, row_ids}`; unmappable → `issue.raised{kind: human_check}`.
