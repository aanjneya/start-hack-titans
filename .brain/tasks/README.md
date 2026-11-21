# Agent tasks: overview (pick what you want to work on)

> Built from the adopted `.brain/PLAN.md` (incl. Tim's truth-store schema §3b) and `.brain/GOAL.md`.
> **Order:** we build all agents + backend first, then the frontend. **Owners assigned Sat 18:10** (frontend later).
> **To claim a task:** write your name next to it in the agent's file (e.g. `T3.2 … — @tim`) and push it with `./scripts/brain-push.sh "claim T3.2"`.

## The agents at a glance

| # | Agent | What it does in one line | Tasks | Owner |
|---|---|---|---|---|
| 0 | [Shared backend](0-shared-backend.md) | The common base: database, message passing between agents, the action log, the AI connection | 9 | ✅ @daksh (done) |
| 1 | [Watcher](1-watcher.md) | Notices every new or changed file on the Soony portal (already running every 15 min) | 5 | @daksh |
| 2 | [Ingest & Normalizer](2-ingest-normalizer.md) | Reads every messy file (CSV, Excel, PDF, Polish/Chinese) and turns it into one clean database | 19 | @daksh |
| 3 | [Reconciler & Gate](3-reconciler-gate.md) | Spots contradictions, missing data and traps, then decides who must handle each problem | 11 | @aanjneya |
| 4 | [Chaser](4-chaser.md) | Politely emails suppliers and carriers for missing answers, in their language, and reads the replies | 6 | @tim |
| 5 | [Launch Planner & Decision Copilot](5-launch-planner.md) | Works out whether we pack everything by Sun 20:00 and proposes fixes with cost vs copies saved | 10 | @jivi |
| 6 | [Risk Rater](6-risk-rater.md) | Gives every part and supplier a risk grade (like Moody's) with the top 3 reasons | 6 | @aanjneya |
| 7 | [Frontend (later)](7-frontend-later.md) | The one screen: what can stop a line, decision cards, risk table, AI trace drawer | 7 | phase 2 |

## Getting started (shared backend is done)
Code lives in `backend/`; read `backend/README.md` (run, test, how to build your agent). Every agent already exists as a **stub** in `backend/agents/<agent>.py` that logs and passes the work on, so the whole loop runs today. Claim tasks here, replace the TODOs in your stub, add a test.

## How they work together (one loop)
```
Watcher → Ingest & Normalizer → Reconciler & Gate ─┬─► Chaser (asks suppliers) ──► replies go back to Ingest
                                                   └─► Launch Planner ──► decision card ──► human approves
Risk Rater feeds Reconciler + Planner.  Every step is written to the agent log (AI traceability).
```

## What we build with
- **Agents + backend:** Python 3.12, FastAPI, SQLModel on SQLite (Tim's schema), Claude API for reading documents and planning.
- **Frontend (phase 2):** Next.js + TypeScript.
- **Data:** the portal mirror already in the repo at `challenge/start-hack-data/data/` (works offline = MOCK mode); live = portal API.

## Build order & milestones
1. **Shared backend (0) first.** Everyone else can start right away with fake data, but needs the shared models to plug in.
2. Agents 1–6 in parallel.
3. **M1 Sat 19:30:** shared backend + database tables on `main`; each agent runs on the real files.
4. **M2 Sat 22:30:** the hero case works end to end through the API (rev B sleeve problem → decision card → approve → forecast improves → log shows every step).
5. **M3 Sun 09:30:** the frontend shows it. **11:00** tag `demo-safe` + record backup video. **12:00** submit.

## ⚠️ The data keeps changing: build for that
The portal data changes all weekend (new files, replaced files, renamed columns, new formats like supplier `profile.json`). We are **not training a model on today's data**: the agents read whatever data is there *each time they run*. So:
- A new drop must flow through the whole loop **automatically** (Watcher → Ingest → Reconciler → Planner) with **no code change**.
- New data adds rows; existing values can be **updated** (filled or corrected) only through `update_row`, which keeps every old value in the change log (`field_changes`). Only Ingest writes data; after any change the Reconciler re-checks automatically.
- Unknown files, folders or columns are still stored and flagged for a human, never crash the run.
- Every agent is tested by replaying the real drops in order (14:15 → 16:08 → 17:23 → whatever comes next). `.brain/DATA_CHANGES.md` lists each drop.
Tasks for this: **T0.8, T1.5, T2.10, T3.10, T5.9** below in the agent files.

## Rules for everyone (from AGENTS.md / PLAN.md)
- Never type numbers from the data into the code: read them from the files (judges check "is anything hardcoded?").
- Every result keeps a link to the file it came from.
- Test with the real drops so far: baseline 14:15, the 16:08 drop (part revisions, launch status) and the 17:23 drop (magnet export ban). See `.brain/DATA_CHANGES.md`.
- Personal branch + merge to `main` every 2–3 h; run `./scripts/verify.sh` before pushing code.
