# Decisions

> Lightweight Architecture Decision Record (ADR) log for **start-hack-titans**.
> Append a short entry only when it saves teammates >15 minutes or settles a real trade-off.

## Template

```markdown
### [HH:MM] <Decision Title> (by <aanjneya|daksh|jivi|tim>)
- **Context**: Why this decision was needed.
- **Decision**: What we chose.
- **Trade-offs**: What we gave up or need to watch out for.
```

---

## Log

### Initial Setup
- **Context**: 4-person hackathon team (`aanjneya`, `daksh`, `jivi`, `tim`) collaborating with multiple coding agents.
- **Decision**: Use `.brain/` pushed automatically to `main` via `scripts/brain-push.sh` (temporary worktree) with 2-tier context loading, `.brain/TASKS.md` lightweight ownership, `API_CONTRACT.ts`, and `MOCK_MODE=true`.
- **Trade-offs**: Dropping lock files relies on `.brain/TASKS.md` + feature-folder isolation + table communication instead of hard locks.

### Pre-Kickoff Infrastructure Hardening (by aanjneya)
- **Context**: SWOT review identified silent-clobber risk in `brain-push.sh`, prompt-only guardrails, Windows symlink risk, doc tax, and Hour-18 branch divergence risk.
- **Decision**:
  1. Upgraded `scripts/brain-push.sh` to 3-way `git merge-file` + safe post-push local snapshot restore + auto-ID detection (`git config hackathon.teammate`).
  2. Added `.githooks/pre-push` (fast-passes `.brain/` & `challenge/`, verifies code on `main`, blocks `.env` & >25MB, has `ALLOW_MAIN_PUSH=1` escape hatch), stack-agnostic `scripts/verify.sh`, and `scripts/setup.sh`.
  3. Replaced `CLAUDE.md` symlink with `@AGENTS.md`, added `.claude/settings.json` allowlist for zero-intervention brain sync, collapsed live docs to 3-file core + 3h staleness guard, and added 2–3h rolling merge cadence.
- **Trade-offs**: `.githooks/pre-push` requires each teammate to run `./scripts/setup.sh <id>` once; Antigravity/Codex users click "Always allow `./scripts/brain-push.sh`" once for zero-prompt auto-sync.

### [17:35] Adopt merged end-to-end plan (by daksh)
- **Context**: Two plan suggestions (aanjneya: 7-step story, data traps, pack simulation, one screen; daksh: closed loop, watcher, hand-offs, build order) needed one direction.
- **Decision**: Adopt the merge as `.brain/PLAN.md`: sell the process (not a product), 5 agents + coordinator, one screen + trace drawer, hero demo = CMP-GAM-004 rev trap with Karton Nowak express offer.
- **Trade-offs**: Dock-scan OCR and supplier scout pushed to P2; owners for the four P0 slices and the stack still open; `GOAL.md` still needs explicit team approval to be filled from the plan.

### [17:45] Truth-store schema + shipments/suppliers tab added to PLAN.md (by tim)
- **Context**: PLAN.md only said "canonical tables with provenance"; all four P0 slices need one concrete schema, and the team worried the end-to-end chain (order → packed) wasn't covered.
- **Decision**: New PLAN.md §3b: one table per chain step (PO → confirmation → shipment → receipt → stock → packout) + `conflicts` + `agent_log`, never overwrite, `part_journey` SQL view. Second UI tab "Shipments & suppliers" reads that view. Schema is the first P0 step.
- **Trade-offs**: Second tab is behind the one-page main view (CPO memo still holds); chain ends at "packed Sun 20:00", no post-Hamburg distribution.

### [18:05] Stack + agents-first build list (by daksh)
- **Context**: Team wants to build all agents + backend first, then the frontend, and pick tasks themselves instead of being assigned.
- **Decision**: Stack = Python 3.12 / FastAPI / SQLModel on SQLite (Tim's §3b schema) + Claude API for agents; Next.js + TS frontend in phase 2. Work is listed per agent in `.brain/tasks/` (README overview + one plain-language file per agent with numbered tasks), all unclaimed.
- **Trade-offs**: Two languages (Python agents, TS UI) means the API contract in `API_CONTRACT.ts` must be kept in sync; owners still to be chosen at the table.

### [18:15] Shared backend landed on main (by daksh)
- **Context**: Every agent needs the same tables, hand-off mechanism, log and Claude helper before people can build in parallel.
- **Decision**: `backend/` with FastAPI + SQLModel/SQLite. Facts are append-only (`add_all`), current truth via `latest_by`; agents talk only through events in `core/events.py`; every action goes to `agent_log`; issues dedupe by key and reopen when they come back; `core/replay.py` replays the real drops from git history. Claude default model `claude-opus-5-5` (env `ANTHROPIC_MODEL`), with server-side refusal fallbacks on `complete()`. All six agents exist as stubs so the loop runs end to end today.
- **Trade-offs**: In-process event bus (no queue service): fine for the demo, one process only. Schema changes to shared tables go through `backend/core/models.py` + `API_CONTRACT.ts` in one small commit to main.

### [19:05] Office-hours learnings applied (by daksh, team-approved)
- **Context**: Soony's head of procurement wants actions not dashboards, a visible reason for every action, approval limits and a human-in-the-loop switch; Lio said our flow looked too sequential and nothing stood out yet.
- **Decision**: Applied proposals P1–P4, P6–P9 from `minutes/office hours 18 uhr - learnings and proposed changes.md` as *vision*: action inbox first; risk → reason → action with one-line summary + reasoning on demand; approval matrix + autonomy switch as customer setup (`backend/setup/soony.json`, `core/policy.py`); agents shown working in parallel; per-agent mistake counts in the trace; standout candidate = magnet export ban case. Skipped P5 (role views): too much for a 3-min pitch, roles stay a non-goal.
- **Trade-offs**: Decision cards gained `summary`, `reasoning`, `evidence`, `needs_approval` (auto-added to local DBs). Default autonomy is `control`, so nothing executes without approval unless a customer setup says `auto`.

### [19:20] Watcher stays a separate agent, owner moved to daksh (by daksh)
- **Context**: Question whether to merge Watcher (1) and Ingest (2).
- **Decision**: Keep them separate in code (Watcher has no AI and only detects changed files; Ingest reads/understands them; the replay feeds Ingest directly). Move Watcher ownership from jivi to daksh (he wrote the 15-min checker), so jivi focuses on the Launch Planner.
- **Trade-offs**: daksh now owns 1 + 2 (the whole intake path); jivi owns 5.

### [19:40] Source of truth is updatable; Ingest is the only data writer (by daksh, team OK)
- **Context**: Append-only made "fill the missing field" awkward; unclear who writes a supplier's answer.
- **Decision**: Rows are the current truth and can be updated only via `store.update_row` (every change → `field_changes`: old, new, source, agent, reason, issue). Only Ingest writes supply-chain data. Loop: Reconciler raises missing-info issue → Chaser emails → reply stored in `inbox` (`reply.received`) → Ingest fills the field (or records a conflict if a different value exists) → `truth.updated` with exact changed rows → Reconciler re-checks and resolves/reopens. `emit_truth_updated` skips empty changes, so no loops. Replaces Tim's "never overwrite" rule in PLAN §3b.
- **Trade-offs**: Conflicting values are not overwritten by default (`on_conflict="record"`); a confirmed correction must say `on_conflict="overwrite"`.

### [20:30] Watcher + Ingest built (by daksh)
- **Context**: Agents 1 and 2 feed everyone else.
- **Decision**: Order confirmations are parsed by rules (one template across KO/ZH/PL/EN suppliers; 202/202 text PDFs), Claude only for the rest (live mode). Column mappings are learned once per header fingerprint (`column_mappings`). Supplier names, vendor numbers and supplier part numbers are mapped through `aliases` (PackRight's PR-CC-0312 → CMP-GAM-003 etc.). Survivorship: same file replaced → overwrite; other source → newest wins + conflict kept; a source that already lost can't flip a value back on reprocessing. Shipment statuses mapped to one list (`in transit` → `in_transit`).
- **Trade-offs**: Scans and free-text documents need live Claude for full reading; offline they carry regex facts (POs, parts, dates, quantities, IBANs, revisions, risk signals).
