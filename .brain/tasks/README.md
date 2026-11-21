# Agent tasks: overview (pick what you want to work on)

> Built from the adopted `.brain/PLAN.md` (incl. Tim's truth-store schema §3b) and `.brain/GOAL.md`.
> **Order:** we build all agents + backend first, then the frontend. Nobody is assigned yet.
> **To claim a task:** write your name next to it in the agent's file (e.g. `T3.2 … — @tim`) and push it with `./scripts/brain-push.sh "claim T3.2"`.

## The agents at a glance

| # | Agent | What it does in one line | Tasks | Owner |
|---|---|---|---|---|
| 0 | [Shared backend](0-shared-backend.md) | The common base: database, message passing between agents, the action log, the AI connection | 7 | unclaimed |
| 1 | [Watcher](1-watcher.md) | Notices every new or changed file on the Soony portal (already running every 15 min) | 4 | unclaimed |
| 2 | [Ingest & Normalizer](2-ingest-normalizer.md) | Reads every messy file (CSV, Excel, PDF, Polish/Chinese) and turns it into one clean database | 9 | unclaimed |
| 3 | [Reconciler & Gate](3-reconciler-gate.md) | Spots contradictions, missing data and traps, then decides who must handle each problem | 9 | unclaimed |
| 4 | [Chaser](4-chaser.md) | Politely emails suppliers and carriers for missing answers, in their language, and reads the replies | 6 | unclaimed |
| 5 | [Launch Planner & Decision Copilot](5-launch-planner.md) | Works out whether we pack everything by Sun 20:00 and proposes fixes with cost vs copies saved | 8 | unclaimed |
| 6 | [Risk Rater](6-risk-rater.md) | Gives every part and supplier a risk grade (like Moody's) with the top 3 reasons | 5 | unclaimed |
| 7 | [Frontend (later)](7-frontend-later.md) | The one screen: what can stop a line, decision cards, risk table, AI trace drawer | 6 | phase 2 |

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

## Rules for everyone (from AGENTS.md / PLAN.md)
- Never type numbers from the data into the code: read them from the files (judges check "is anything hardcoded?").
- Every result keeps a link to the file it came from.
- Test with the real drops so far: baseline 14:15, the 16:08 drop (part revisions, launch status) and the 17:23 drop (magnet export ban). See `.brain/DATA_CHANGES.md`.
- Personal branch + merge to `main` every 2–3 h; run `./scripts/verify.sh` before pushing code.
