# Backend (shared base + agents)

Python 3.12+, FastAPI, SQLModel on SQLite, Claude via the Anthropic SDK.
Plan: `.brain/PLAN.md` · agent tasks: `.brain/tasks/` · API types: `API_CONTRACT.ts`.

## Run it (from the repo root)

```bash
python3 -m venv backend/.venv
backend/.venv/bin/pip install -r backend/requirements.txt
backend/.venv/bin/pytest backend/tests -q            # all green before you push
backend/.venv/bin/uvicorn backend.app.main:app --reload --port 8000
```

Then:
- `GET  localhost:8000/api/health`: mock mode + registered agents
- `POST localhost:8000/api/run`: push the whole portal mirror through all agents
- `GET  localhost:8000/api/trace`: what every agent did (newest first)
- `GET  localhost:8000/api/changes`: change log of the source of truth · `POST /api/inbox`: feed a supplier reply into the loop
- `backend/.venv/bin/python -m backend.core.replay --list`: the real drops found in git history
- `backend/.venv/bin/python -m backend.core.replay`: reset the DB and replay all drops in order
- `POST /api/replay/step`: the next real drop arrives (demo button; first call = baseline on an empty store)
- `POST /api/watch/check`: live portal check now (needs `MOCK_MODE=false` + `SOONY_API_TOKEN`; timer via `WATCH_INTERVAL_MIN`)

`MOCK_MODE=true` (default, from `.env`) = fully offline: no Claude calls, no emails, no portal calls.
Set `MOCK_MODE=false` + `ANTHROPIC_API_KEY` (or `ant auth login`) to use Claude for real.

## Layout

```
backend/
  app/main.py          FastAPI routes (ApiResponse envelope, see API_CONTRACT.ts)
  system.py            wires the Coordinator + one instance of every agent; process_drop(), run_full()
  core/config.py       settings from env/.env (nothing customer-specific in code)
  core/models.py       truth-store tables (PLAN.md §3b) + Issue, DecisionCard, Forecast, RiskScore, Outbox, AgentLog
  core/store.py        engine/session, add_all (new rows), update_row (logged changes), latest_by, find_rows, part_journey view, query()
  core/events.py       event names agents hand work over with
  core/coordinator.py  in-process event bus; errors are logged, never stop other agents
  core/trace.py        agent log (AI traceability)
  core/llm.py          complete() / extract() with Claude (extract(files=[...]) for images/PDFs = OCR); MOCK via register_mock()
  core/parsing.py      CSV sniffing (, ; tab |), xlsx/docx/pdf text, safe paths
  core/replay.py       rebuild each real drop from git history and replay it (T0.8)
  core/policy.py       customer setup: approver_for(amount), needs_approval(amount) from setup/<customer>.json
  setup/soony.json     Soony's approval matrix + autonomy switch (office hours Sat 18:00)
  agents/base.py       Agent base class: subscriptions, log(), emit(), raise_issue() (dedup + reopen)
  agents/watcher.py    ✅ agent 1: portal diff (live) / replay (demo)
  agents/ingest*.py    ✅ agent 2: tables (learned column mappings), documents (OA parser, facts, Claude), aliases, replies
  agents/*.py          the other agents are still STUBS: replace the TODOs (see .brain/tasks/<n>-<agent>.md)
  tests/               pytest; each test gets its own temp DB
```

## How to build your agent

1. Claim the tasks in `.brain/tasks/<n>-<agent>.md` (add `— @you`).
2. Open `backend/agents/<agent>.py`, keep the class name / `name` / subscriptions, replace the TODOs.
3. Read files from `Path(event.payload["data_root"])`, never a hardcoded path (replay uses snapshots).
4. New rows: `store.add_all([...])` with `source=` (path relative to the data root). Changing existing values: `self.update_row(Model, row_id, {...}, source=..., reason=...)` (logged in `field_changes`). **Only Ingest writes supply-chain data**; announce changes with `self.emit_truth_updated(...)`.
5. Problems: `self.raise_issue(type=..., kind=IssueKind.MISSING_INFO|COPIES_AT_RISK|HUMAN_CHECK, key=..., evidence=[Evidence(path=..., locator=...)])`.
6. Claude: `llm.extract(prompt, MyPydanticModel, agent=self.name, task="...")` and register a mock for offline demos.
7. Add a test in `backend/tests/test_<agent>.py` that replays the real drops (`backend.core.replay`).
