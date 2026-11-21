# 1 · Watcher

**Owner:** @daksh (moved from jivi Sat 19:20) · ✅ **built Sat ~19:35** in `backend/agents/watcher.py`: live portal diff (SHA-256 manifest `portal_files`, downloads only changed files to `backend/data/portal/`), timer `WATCH_INTERVAL_MIN`, `POST /api/watch/check`; demo/offline: `POST /api/replay/step` replays the real drops one by one.

## What this agent does
It checks the Soony portal every 15 minutes and notices when files are added, changed or removed. It downloads only those files and tells the next agent "these files are new". It never interprets anything; it only notices.

## Why we need it
The brief says data "changes without notice", and the judges ask: "When a new drop arrives, does your system cope, or do you rebuild it?" The Watcher is how our system reacts on its own.

## Example from our real data
At 16:08 it saw 5 new files (launch status, CPO memo, part revision changes) and 1 changed file (`products.csv` with renamed columns). At 17:23 it saw the magnet export-ban news and the PackRight supply notice.

## What it works with
- **Gets:** the portal's file list with a fingerprint (SHA-256) per file.
- **Gives:** a list of new, changed and removed files → **Ingest & Normalizer**.

## Tasks
- [x] **T1.1** Move the working checker (`monitor/drop_watch.py`, currently on Daksh's laptop) into the repo as the Watcher agent.
- [x] **T1.2** Instead of only writing a report, hand the list of changed files to the Ingest agent.
- [x] **T1.3** Demo mode: replay the real drops from `challenge/start-hack-data/versions/` and `data/` so we can show "a new drop arrives" on stage without Wi-Fi.
- [x] **T1.4** Write each check to the agent log ("16:08 · 5 added · 1 changed").
- [x] **T1.5** Live data: run on a timer inside the backend so new drops are picked up without anyone pressing a button; removed files are reported too.

## When it's done
- Replaying 14:15 → 16:08 → 17:23 produces exactly the file changes listed in `.brain/DATA_CHANGES.md`.
- In live mode it picks up a real new drop within 15 minutes.

---
### Technical notes (for Claude Code, refine later)
- Reuse from `monitor/drop_watch.py`: `get()`, manifest diff by `sha256`, `safe_rel()` path check, `office_text()`/`csv_summary()` (move these two into a shared `backend/core/parsing.py` for the Ingest agent).
- Token: `SOONY_API_TOKEN` + `SOONY_BASE_URL` in `.env` (add placeholders to `.env.example`). Endpoint `GET /api/v1/files` → `{files:[{path, bytes, sha256, url}]}`.
- Emit `files.changed{added[], changed[], removed[]}`. The existing 15-min data log to GitHub keeps running separately; don't break it.
