# start-hack-data · Lio portal drop log

> Maintained automatically by Daksh's Lio drop-watch agent. **Do not edit this folder by hand**;
> it is overwritten on every check. Soony data: use only for START Hack Munich 2026.

Every ~15 minutes the agent lists all files on the Soony supplier portal (`/api/v1/files`, SHA-256 per file),
downloads only what was added or changed, mirrors it into [`data/`](data/), writes a diff report to
[`reports/`](reports/) and commits it here. Older versions of any file are in this folder's git history
(`git log -p -- start-hack-data/data/<path>`).

## Latest check

### Drop check 2026-11-21T14-30 (Europe/Berlin)
Portal now: 308 files, 47.2 MB
No changes since last check.

## All checks (newest first, Europe/Berlin)

| Check | Portal | Added | Changed | Removed | What changed | Details |
|---|---|---|---|---|---|---|
| 2026-11-21 14-30 | 308 · 47.2 MB | 0 | 0 | 0 | no changes | [report](reports/2026-11-21T14-30.md) |
