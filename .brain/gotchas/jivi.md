
- New untracked handoffs can fail brain sync when plain `git rev-parse HEAD:<path>` echoes the unresolved ref. Use `git rev-parse --verify --quiet` for both local and remote blob lookups in the sync helper.

- **Issue** — Replaying all drops for the sleeve rush-print and pickup/air tests made them depend on an open delivery. The 21:38 receipt closed PO 4500318775/2 on SHP-2026-04313.
- **Fix** — Pin those scenarios to the 17:23 data commit `f832f46`. Count outbox messages by decision ID. Run `backend/.venv/bin/pytest backend/tests/test_planner.py -q` for both the historical scenarios and the all-drops check that a known shortfall always gets a recovery recommendation.
