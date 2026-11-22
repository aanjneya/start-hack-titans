# DATA_CHANGES — Soony portal drops (auto-generated, do not edit)

Written by Daksh's Lio drop-watch agent after every ~15-min check of the supplier portal.
Read this before building on portal data: files appear and change without notice.
Full per-check log, reports and every file version: `challenge/start-hack-data/README.md`,
`challenge/start-hack-data/reports/`, `challenge/start-hack-data/versions/<check>/` (old copies), `challenge/start-hack-data/data/` (current).

**Last check:** 2026-11-22 08-53 Berlin · 332 files, 53.2 MB · no changes

## Checks with changes (newest first)

- **2026-11-21 21-38** — 14 added · 0 changed · 0 removed: `soony/erp/goods_receipts_update.csv`, `soony/erp/stock_update_2026-11-21.xlsx`, `soony/receiving/DC-HAM/20260903_DN-260831-382.jpg`, `soony/receiving/DC-HAM/20260903_WZ-260902-594.jpg` …
  - Evening drop: 12 new delivery-note scans, a goods-receipt update and book stock after the late shift. Packed at 20:50: Standard 602,000 (38,000 left), Collector's 60,300 (11,700 left). Book stock shows clear cases CMP-GAM-003 at only 6,000 vs ~38,000 Standard still to pack, while 350,000 cases sit at CTA Hamburg (SHP-2026-04276, ready for pick-up); game cards CMP-GAM-001 at 42,900 vs ~49,700 needed (Hwaseong delivered 28,000 of 48,000, rest after launch). New traps: one pallet booked as GH6 sleeves CMP-GAM-004 holds 4,000 GH5 sleeves; and Art Book book stock 77,800 still includes the 72,000 phantom books from WZ-261116-473, so real stock is ~5,800 vs 11,700 Collector's copies left.
  - report: `challenge/start-hack-data/reports/2026-11-21T21-38.md`
- **2026-11-21 17-23** — 5 added · 0 changed · 0 removed: `soony/news/2026-11-21_export_controls.json`, `suppliers/SUP-0018_Pune_Diecast_Works/profile.json`, `suppliers/SUP-0029_Guangzhou_PackRight/components_delta.csv`, `suppliers/SUP-0029_Guangzhou_PackRight/notices/Supply_notice_export_licence_2026-11-21.pdf` …
  - Export-control drop: news says China now needs end-use licences for NdFeB magnets (4-8 weeks), and Guangzhou PackRight (SUP-0029) says its magnet sub-supplier cannot export. CMP-MAG-005 (4 lid magnets per Collector's box) is single-source with stock 0, no PO, no receipts; that answers the onboarding question 'why does the stock list show no lid magnets?' only if the magnets ship inside the boxes. Collector's box CMP-GAM-007 stock is 27,500 vs ~18,900 still to pack, but its lead time jumps from 63 to 105 days for December deliveries. Pune Diecast (statue second source) now shows status 'watch' with its conditional release expired on 30 Oct and not extended; profiles now also come as SAP JSON (new format next to the old CSV).
  - report: `challenge/start-hack-data/reports/2026-11-21T17-23.md`
- **2026-11-21 16-08** — 5 added · 1 changed · 0 removed: `soony/context/launch_status.xlsx`, `soony/context/memo_launch_priorities.docx`, `soony/context/org_chart_v2.pdf`, `soony/master/bom_delta_2026-11-21.csv` …
  - Big drop: 5 new files plus a schema change. launch_status.xlsx (as of Sat 13:00): Standard 86k left at 6,000/h (~14h), Collector's 18.9k left at 900/h (~21h of the ~23 packing hours left), so Collector's has almost no slack. New CPO memo: Collector's gets short parts first, nothing ships without Quality release, rush/air above policy goes to the CPO within the hour, and he wants one page on what can stop a line, not forty dashboards. Engineering changes: CMP-GAM-004 (Standard) moves to rev C and rev B must NOT be packed; CMP-GAM-009 (Collector's) moves to rev B but rev A can be used up; CMP-GAM-010 is now flagged critical, yet its two open sea shipments arrive in December. products.csv renamed plan_weekly_units to weekly_plan_qty and added day_one_qty and valid_from, so hardcoded parsers break.
  - report: `challenge/start-hack-data/reports/2026-11-21T16-08.md`
