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
