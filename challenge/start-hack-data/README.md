# start-hack-data · Lio portal drop log

> Maintained automatically by Daksh's Lio drop-watch agent. **Do not edit this folder by hand**;
> it is overwritten on every check. Soony data: use only for START Hack Munich 2026.

Every ~15 minutes the agent lists all files on the Soony supplier portal (`/api/v1/files`, SHA-256 per file),
downloads only what was added or changed, mirrors it into [`data/`](data/), writes a diff report to
[`reports/`](reports/) and commits it here. **Nothing is ever lost:** before a changed or removed file is
updated in `data/`, its previous copy is saved to [`versions/<check>/`](versions/), and git history keeps every version too.
Team summary: [`.brain/DATA_CHANGES.md`](../../.brain/DATA_CHANGES.md).

## Latest check

### Drop check 2026-11-21T17-23 (Europe/Berlin)
Portal now: 318 files, 47.4 MB
**5 added · 0 changed · 0 removed**
Where: `soony/news` (1), `suppliers/SUP-0018_Pune_Diecast_Works` (1), `suppliers/SUP-0029_Guangzhou_PackRight` (3)

## Watch-list hits (Lio brief signals)
- 🔥 `soony/news/2026-11-21_export_controls.json`: export
- 🔥 `suppliers/SUP-0018_Pune_Diecast_Works/profile.json`: export
- 🔥 `suppliers/SUP-0029_Guangzhou_PackRight/components_delta.csv`: export
- 🔥 `suppliers/SUP-0029_Guangzhou_PackRight/notices/Supply_notice_export_licence_2026-11-21.pdf`: notices
- 🔥 `suppliers/SUP-0029_Guangzhou_PackRight/profile.json`: export

### ➕ `soony/news/2026-11-21_export_controls.json` (1,143 B)
```
{
  "generated_at": "2026-11-20T18:00:00+01:00",
  "items": [
    {
      "id": "NEWS-2026-1121-04",
      "published_at": "2026-11-21T16:00:00+01:00",
      "source": "Asia Commodities Wire",
      "headline": "China tightens export licensing for rare-earth magnets",
```

### ➕ `suppliers/SUP-0018_Pune_Diecast_Works/profile.json` (1,054 B)
```
{
  "exportedAt": "2026-11-21T17:12:00+01:00",
  "source": "Soony vendor master (SAP XK03 extract v2)",
  "supplier": {
    "vendorNo": 18,
    "supplierId": "SUP-0018",
    "name": "Pune Diecast Works",
    "legalName": "Pune Diecast Works Pvt. Ltd.",
```

### ➕ `suppliers/SUP-0029_Guangzhou_PackRight/components_delta.csv` (276 B)
```
component_id,supplier_id,action,lead_time_days_old,lead_time_days_new,share,primary,note
CMP-GAM-007,SUP-0029,flag,63,105,1.0,Y,"+42 d for deliveries from December, see supply notice"
CMP-MAG-005,SUP-0029,flag,63,,1.0,Y,sub-supplier cannot export; no second source on file
```

### ➕ `suppliers/SUP-0029_Guangzhou_PackRight/notices/Supply_notice_export_licence_2026-11-21.pdf` (73,532 B)

### ➕ `suppliers/SUP-0029_Guangzhou_PackRight/profile.json` (1,006 B)
```
{
  "exportedAt": "2026-11-21T17:12:00+01:00",
  "source": "Soony vendor master (SAP XK03 extract v2)",
  "supplier": {
    "vendorNo": 29,
    "supplierId": "SUP-0029",
    "name": "Guangzhou PackRight",
    "legalName": "Guangzhou PackRight Packaging Co., Ltd.",
```

## Agent feedback
Export-control drop: news says China now needs end-use licences for NdFeB magnets (4-8 weeks), and Guangzhou PackRight (SUP-0029) says its magnet sub-supplier cannot export. CMP-MAG-005 (4 lid magnets per Collector's box) is single-source with stock 0, no PO, no receipts; that answers the onboarding question 'why does the stock list show no lid magnets?' only if the magnets ship inside the boxes. Collector's box CMP-GAM-007 stock is 27,500 vs ~18,900 still to pack, but its lead time jumps from 63 to 105 days for December deliveries. Pune Diecast (statue second source) now shows status 'watch' with its conditional release expired on 30 Oct and not extended; profiles now also come as SAP JSON (new format next to the old CSV).

## All checks (newest first, Europe/Berlin)

| Check | Portal | Added | Changed | Removed | What changed | Details |
|---|---|---|---|---|---|---|
| 2026-11-21 17-23 | 318 · 47.4 MB | 5 | 0 | 0 | **5 added · 0 changed · 0 removed** — `soony/news/2026-11-21_export_controls.json`, `suppliers/SUP-0018_Pune_Diecast_Works/profile.json`, `suppliers/SUP-0029_Guangzhou_PackRight/components_delta.csv` … Export-control drop: news says China now needs end-use licences for NdFeB magnets (4-8 weeks), and Guangzhou PackRight (SUP-0029) says its magnet sub-supplier cannot export. CMP-MAG-005 (4 lid magnets per Collector's box) is single-source with stock 0, no PO, no receipts; that answers the onboarding question 'why does the stock list show no lid magnets?' only if the magnets ship inside the boxes. Collector's box CMP-GAM-007 stock is 27,500 vs ~18,900 still to pack, but its lead time jumps from 63 to 105 days for December deliveries. Pune Diecast (statue second source) now shows status 'watch' with its conditional release expired on 30 Oct and not extended; profiles now also come as SAP JSON (new format next to the old CSV). | [report](reports/2026-11-21T17-23.md) |
| 2026-11-21 17-08 | 313 · 47.3 MB | 0 | 0 | 0 | **no changes** | [report](reports/2026-11-21T17-08.md) |
| 2026-11-21 16-53 | 313 · 47.3 MB | 0 | 0 | 0 | **no changes** | [report](reports/2026-11-21T16-53.md) |
| 2026-11-21 16-38 | 313 · 47.3 MB | 0 | 0 | 0 | **no changes** | [report](reports/2026-11-21T16-38.md) |
| 2026-11-21 16-23 | 313 · 47.3 MB | 0 | 0 | 0 | **no changes** | [report](reports/2026-11-21T16-23.md) |
| 2026-11-21 16-08 | 313 · 47.3 MB | 5 | 1 | 0 | **5 added · 1 changed · 0 removed** — `soony/context/launch_status.xlsx`, `soony/context/memo_launch_priorities.docx`, `soony/context/org_chart_v2.pdf` … Big drop: 5 new files plus a schema change. launch_status.xlsx (as of Sat 13:00): Standard 86k left at 6,000/h (~14h), Collector's 18.9k left at 900/h (~21h of the ~23 packing hours left), so Collector's has almost no slack. New CPO memo: Collector's gets short parts first, nothing ships without Quality release, rush/air above policy goes to the CPO within the hour, and he wants one page on what can stop a line, not forty dashboards. Engineering changes: CMP-GAM-004 (Standard) moves to rev C and rev B must NOT be packed; CMP-GAM-009 (Collector's) moves to rev B but rev A can be used up; CMP-GAM-010 is now flagged critical, yet its two open sea shipments arrive in December. products.csv renamed plan_weekly_units to weekly_plan_qty and added day_one_qty and valid_from, so hardcoded parsers break. | [report](reports/2026-11-21T16-08.md) |
| 2026-11-21 15-53 | 308 · 47.2 MB | 0 | 0 | 0 | **no changes** | [report](reports/2026-11-21T15-53.md) |
| 2026-11-21 15-38 | 308 · 47.2 MB | 0 | 0 | 0 | **no changes** | [report](reports/2026-11-21T15-38.md) |
| 2026-11-21 15-23 | 308 · 47.2 MB | 0 | 0 | 0 | **no changes** | [report](reports/2026-11-21T15-23.md) |
| 2026-11-21 15-08 | 308 · 47.2 MB | 0 | 0 | 0 | **no changes** | [report](reports/2026-11-21T15-08.md) |
| 2026-11-21 15-02 | 308 · 47.2 MB | 0 | 0 | 0 | **no changes** | [report](reports/2026-11-21T15-02.md) |
| 2026-11-21 14-51 | 308 · 47.2 MB | 0 | 0 | 0 | **no changes** | [report](reports/2026-11-21T14-51.md) |
| 2026-11-21 14-36 | 308 · 47.2 MB | 0 | 0 | 0 | **no changes** | [report](reports/2026-11-21T14-36.md) |
| 2026-11-21 14-32 | 308 · 47.2 MB | 0 | 0 | 0 | **no changes** | [report](reports/2026-11-21T14-32.md) |
| 2026-11-21 14-30 | 308 · 47.2 MB | 0 | 0 | 0 | **no changes** First published check. Portal unchanged since the 14:15 baseline (308 files): no new supplier notices, shipment updates or order confirmations yet. | [report](reports/2026-11-21T14-30.md) |
