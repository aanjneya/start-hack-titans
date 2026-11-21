# 2 · Ingest & Normalizer

**Owner:** @daksh · ✅ **built Sat ~19:35** (`backend/agents/ingest.py`, `ingest_mapping.py`, `ingest_docs.py`; tests `backend/tests/test_ingest_watcher.py`). All 318 files in ~1.5 s, no errors.

**Scans (OCR) built Sat ~19:55:** `llm.extract(..., files=[...])` sends images/PDFs to Claude vision; each delivery note is cross-checked (number vs file name, PO known, parts resolved, quantity vs the goods receipt with the same delivery-note number, handwritten notes kept) and cached per file hash, so reruns cost nothing. **Still open:** OCR, document summaries and the Claude fallback for unmatched part numbers only run with `MOCK_MODE=false` + an API key (not tested live yet); offline, scans stay "pending" with file-name facts.

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
- [x] **T2.1** Read the ERP spreadsheets in `soony/erp/`: orders (both formats), shipments, goods receipts, stock, pack-out log.
- [x] **T2.2** Read the master data in `soony/master/`: parts, BOM (parts per box), products, suppliers, routes, ports, carriers, plus the change files (`bom_delta_…`, `components_update_…`).
- [x] **T2.3** Read the Excel files: the logistics tracker (`soony/logistics/…v37_FINAL_final2.xlsx`) and `soony/context/launch_status.xlsx`.
- [x] **T2.4** Read each supplier's folder in `suppliers/<ID>_<Name>/`: profile (old CSV and new SAP JSON), parts list, `components_delta.csv`.
- [x] **T2.5** Read PDFs with AI: order confirmations (`order_confirmations/OA…_<PO>.pdf`), notices (often Polish/Chinese), contracts. Pull out quantity, date, part revision, price and bank details, translated to English.
- [x] **T2.6** Read the news file (`soony/news/…json`) and memos (`soony/context/*.docx`).
- [x] **T2.7** Map supplier part and order numbers to our `CMP-…` / `4500…` numbers, with a confidence score.
- [x] **T2.8** Let AI match column names by meaning, so renamed or new columns still work.
- [x] **T2.9** Store a source and timestamp on every row; change existing values only via `self.update_row(...)` (logged in `field_changes`).
- [x] **T2.10** Live data: replaced files add new facts (old ones kept as history); unknown new files, folders or formats are still stored and flagged, never crash the run.

- [x] **T2.19** You are the **only writer** of supply-chain data: apply supplier replies from the `inbox` (`on_reply`): extract the answer (quote + confidence), `update_row` the right row (fills empty fields, records a conflict if a different value exists), mark the message processed, then `emit_truth_updated` so the Reconciler re-checks.

## Techniques from research (how Rossum, Tamr, Informatica, project44 do it: `.brain/Suggestions/ingest-research-data-unification.md`)
- [x] **T2.11** Learn each file format once: save the column mapping under a fingerprint of the file's headers; Claude only maps new header sets (headers + 5 sample rows + our tables + SAP glossary), known ones are read by plain code.
- [x] **T2.12** Plain-code parsing after mapping: the saved mapping decides delimiter, decimal comma and date format; values that don't parse are rejected and flagged, never guessed.
- [x] **T2.13** Sort PDFs by type first (order confirmation / notice / contract / other), then extract a fixed set of fields per type.
- [x] **T2.14** Grounding: every value Claude pulls from a PDF comes with the exact quote and page; code checks the quote really is in the PDF, otherwise the value is dropped.
- [x] **T2.15** Confidence from checks: order exists, quantity × price ≈ total, dates make sense, quote found. Below the threshold → a "needs human check" issue instead of a silent value.
- [x] **T2.16** Match suppliers and parts: saved name list first; else strip legal suffixes ("Sp. z o.o.", "Co., Ltd."), take the 5 closest names, let Claude pick one or "none" with a reason; save confirmed matches so it learns.
- [x] **T2.17** When sources disagree, pick the current value by source trust → confidence → newest, and keep the disagreement visible (`conflicts` table).
- [x] **T2.18** One standard shipment status list: translate ERP, tracker and carrier wording (e.g. `arrived_port`, "in transit") into the same events (also helps the Reconciler).

## When it's done
- One run on the real data fills every table, and every row shows its source file.
- The old and new `products.csv` both load correctly.
- Karton Nowak's Polish notices come out as readable English facts (downtime dates, express offer).

---
### Technical notes (for Claude Code, refine later)
- Tables: PLAN.md §3b. Data root: `challenge/start-hack-data/data/` (`soony/…`, `suppliers/…`). Reuse `office_text()` (xlsx/docx) from `monitor/drop_watch.py`; PDFs: `pypdf` text (`core/parsing.pdf_text`) → `llm.extract(...)` with a Pydantic schema per document type, each field with `quote` + `page`; keep the original text in `documents`.
- New small tables for T2.11/T2.16 (add to `core/models.py` in one tiny commit): `column_mappings(header_fingerprint, file_pattern, mapping JSON, created_by, confirmed)` and `aliases(kind supplier|part|po, alias, canonical_id, confidence, source, confirmed)`.
- Legacy PO: `EBELN=po_number, EBELP=po_line, LIFNR=vendor no. (map via profile vendorNo), MATNR=material_id, WERKS=plant, MENGE=quantity, NETPR=unit_price (decimal comma), BEDAT=order_date, EINDT=requested_date, ZZCONF=promised_date, WEDAT=actual_delivery, ZZSTAT=status code`. Dates `MM/DD/YYYY`.
- Shipments: split `po_numbers` and `material_ids` on `|` into link tables. Tracker xlsx uses supplier *names* and `", "`-separated POs.
- Emit `truth.updated{tables, row_ids}`; unmappable → `issue.raised{kind: human_check}`.
