# start-hack-data · Lio portal drop log

> Maintained automatically by Daksh's Lio drop-watch agent. **Do not edit this folder by hand**;
> it is overwritten on every check. Soony data: use only for START Hack Munich 2026.

Every ~15 minutes the agent lists all files on the Soony supplier portal (`/api/v1/files`, SHA-256 per file),
downloads only what was added or changed, mirrors it into [`data/`](data/), writes a diff report to
[`reports/`](reports/) and commits it here. **Nothing is ever lost:** before a changed or removed file is
updated in `data/`, its previous copy is saved to [`versions/<check>/`](versions/), and git history keeps every version too.
Team summary: [`.brain/DATA_CHANGES.md`](../../.brain/DATA_CHANGES.md).

## Latest check

### Drop check 2026-11-21T15-23 (Europe/Berlin)
Portal now: 308 files, 47.2 MB
No changes since last check.

## All checks (newest first, Europe/Berlin)

| Check | Portal | Added | Changed | Removed | What changed | Details |
|---|---|---|---|---|---|---|
| 2026-11-21 15-23 | 308 · 47.2 MB | 0 | 0 | 0 | **no changes** | [report](reports/2026-11-21T15-23.md) |
| 2026-11-21 15-08 | 308 · 47.2 MB | 0 | 0 | 0 | **no changes** | [report](reports/2026-11-21T15-08.md) |
| 2026-11-21 15-02 | 308 · 47.2 MB | 0 | 0 | 0 | **no changes** | [report](reports/2026-11-21T15-02.md) |
| 2026-11-21 14-51 | 308 · 47.2 MB | 0 | 0 | 0 | **no changes** | [report](reports/2026-11-21T14-51.md) |
| 2026-11-21 14-36 | 308 · 47.2 MB | 0 | 0 | 0 | **no changes** | [report](reports/2026-11-21T14-36.md) |
| 2026-11-21 14-32 | 308 · 47.2 MB | 0 | 0 | 0 | **no changes** | [report](reports/2026-11-21T14-32.md) |
| 2026-11-21 14-30 | 308 · 47.2 MB | 0 | 0 | 0 | **no changes** First published check. Portal unchanged since the 14:15 baseline (308 files): no new supplier notices, shipment updates or order confirmations yet. | [report](reports/2026-11-21T14-30.md) |
