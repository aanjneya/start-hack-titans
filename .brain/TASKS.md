# Live Task Board

> Lightweight shared board synced via `.brain/` on `main` (replaces separate lock files).
> Format: `[ ]` todo, `[~] @<id> Task (active/folder/, shared-hub-file)` in progress, `[x] @<id>` done.
> **Collision check**: Always list active folders and shared hub files (`API_CONTRACT.ts`, `package.json`, root router/layout, DB schema) in parentheses on your `[~]` task. Warn the user before editing a path listed on another teammate's active `[~]` task.

## Now (Active Sprint)

- [ ] Drop START Hack Munich challenge briefs & rubric into `challenge/`
- [ ] Fill in `.brain/GOAL.md` (Core Goal, Must-Have Scope, Non-Goals)
- [ ] Define initial endpoints & types in `API_CONTRACT.ts` + mock payloads
- [ ] Fill in `.brain/DESIGN_SYSTEM.md` and `.brain/DEMO_SCRIPT.md`

## Next

- [ ] _Add vertical slices here once the challenge is chosen_

## Done

- [x] `@aanjneya` Initialize 4-person hackathon repo, `second-brain` skill, `AGENTS.md`, and symlinks
- [x] `@aanjneya` Harden hackathon infra: 3-way `brain-push.sh`, `.githooks/pre-push`, `verify.sh`, `setup.sh`, `@AGENTS.md`, zero-intervention brain sync
