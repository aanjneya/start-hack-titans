# 0 · Shared backend (build first)

**Owner:** @daksh: ✅ **done Sat 18:15** (code in `backend/`, how-to in `backend/README.md`)

## What this is
Not an agent itself, but the common base all agents stand on: the database (one table per supply step, Tim's schema), a simple way for agents to pass work to each other, a log of everything every agent does, and one shared connection to Claude.

## Why we need it
Without one shared base, five people build five incompatible agents. The action log is also what proves "AI traceability" to the judges and to Nils (IT).

## Example
The Reconciler finds a problem and hands it to the Planner. The base delivers it, and the log records: "17:40 Reconciler raised issue #12 (rev B sleeves blocked), evidence: bom_delta_2026-11-21.csv".

## What it works with
- **Gets:** nothing from outside; every agent uses it.
- **Gives:** database tables, the hand-off mechanism, the log, the AI helper, the on/off switch for demo mode.

## Tasks
- [x] **T0.1** Set up the Python project in `backend/` (FastAPI, SQLModel, run script, requirements).
- [x] **T0.2** Create the database tables from PLAN.md §3b: master, supply chain, `conflicts`, `agent_log`, plus the `part_journey` view.
- [x] **T0.3** Add shared result types: Issue (a problem found), Decision card (options + cost + copies), Evidence (which file and which row).
- [x] **T0.4** Build the hand-off mechanism: an agent says "issue raised" and the right next agent gets it.
- [x] **T0.5** Make every agent action write a row to `agent_log` automatically.
- [x] **T0.6** Write one shared Claude helper with a demo-mode switch (`MOCK_MODE=true` gives fixed answers and works with no internet).
- [x] **T0.7** Expose the API the frontend will use later, and write the same routes into `API_CONTRACT.ts`.
- [x] **T0.10** One Finding format v1 for all hand-offs (`.brain/DATA_FORMAT.md`, `models.Finding/Impact/SuggestedAction`, validated in `raise_issue`; event envelope `_meta`).
- [x] **T0.9** Updatable source of truth (team decision Sat 19:30): `store.update_row` / `Agent.update_row` with change log `field_changes`, `inbox` table, `system.store_reply` / `receive_reply`, `Agent.emit_truth_updated` (no-op without changes), `GET /api/changes`, `GET|POST /api/inbox`, loop test `tests/test_update_loop.py`.
- [x] **T0.8** Live data: when the Watcher reports a new drop, rerun the whole loop automatically, and add a "replay all drops in order" test command that every agent uses.

## When it's done
- `GET /api/health` answers and shows whether demo mode is on.
- Each agent can save a row, raise an issue and see it in the log.
- It runs with the Wi-Fi off in demo mode.

---
### Technical notes (for Claude Code, refine later)
- Layout: `backend/app/main.py`, `backend/core/{models,store,coordinator,trace,llm,config}.py`, `backend/agents/*.py`, `backend/tests/`. DB file `backend/data/truth.db` (gitignore).
- Tables per PLAN §3b: `materials, bom, products, supplier_part_map, purchase_orders, order_confirmations, shipments (+ shipment_po / shipment_material link tables), goods_receipts, stock, packout, conflicts, agent_log`; every row has `source`, `ingested_at` (+ `confidence` if extracted). Never overwrite; current truth = derived view with precedence goods receipt > tracker > supplier promise.
- Events: `files.changed → ingest`, `truth.updated → reconciler, risk`, `issue.raised{kind: missing_info|copies_at_risk|human_check} → chaser|planner|human`, `decision.approved → planner.execute`, `reply.received → ingest`. In-process bus is enough.
- API: `GET /api/health, POST /api/run, GET /api/issues, GET /api/forecast, GET /api/decisions, POST /api/decisions/{id}/approve|reject, GET /api/trace, GET /api/risk, GET /api/outbox, GET /api/part-journey`.
- LLM (built): `backend/core/llm.py` → `complete()` (beta server-side refusal fallbacks) and `extract()` (Pydantic structured output via `messages.parse`); model `ANTHROPIC_MODEL` (default `claude-opus-5-5`), effort `LLM_EFFORT` (default medium); MOCK via `register_mock(task, fn)`.
- Built extras: `core/replay.py` (rebuild every real drop from git history, `python -m backend.core.replay`), `agents/base.py` (`raise_issue` dedups by key and reopens resolved issues), `system.py` (`process_drop`, `run_full`), 8 pytest tests.
