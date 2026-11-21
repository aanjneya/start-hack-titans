# start-hack-data · Lio portal drop log

> Maintained automatically by Daksh's Lio drop-watch agent. **Do not edit this folder by hand**;
> it is overwritten on every check. Soony data: use only for START Hack Munich 2026.

Every ~15 minutes the agent lists all files on the Soony supplier portal (`/api/v1/files`, SHA-256 per file),
downloads only what was added or changed, mirrors it into [`data/`](data/), writes a diff report to
[`reports/`](reports/) and commits it here. **Nothing is ever lost:** before a changed or removed file is
updated in `data/`, its previous copy is saved to [`versions/<check>/`](versions/), and git history keeps every version too.
Team summary: [`.brain/DATA_CHANGES.md`](../../.brain/DATA_CHANGES.md).

## Latest check

### Drop check 2026-11-21T17-08 (Europe/Berlin)
Portal now: 313 files, 47.3 MB
No changes since last check.

## All checks (newest first, Europe/Berlin)

| Check | Portal | Added | Changed | Removed | What changed | Details |
|---|---|---|---|---|---|---|
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
