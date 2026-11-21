# Live Task Board

> Lightweight shared board synced via `.brain/` on `main` (replaces separate lock files).
> Format: `[ ]` todo, `[~] @<id> Task (active/folder/, shared-hub-file)` in progress, `[x] @<id>` done.
> **Collision check**: Always list active folders and shared hub files (`API_CONTRACT.ts`, `package.json`, root router/layout, DB schema) in parentheses on your `[~]` task. Warn the user before editing a path listed on another teammate's active `[~]` task.

## Now (Active Sprint)

- [ ] Register team on Lio Customer Portal (`start-hack-portal-cxh3crbdaef2f8gt.westeurope-01.azurewebsites.net`), grab API token, download `/api/v1/archive.zip`, and register sender emails
- [ ] Book Office Hours (Sat 14:00 / Sat 17:00 / Sun 09:00) with Dr. Maren Vogt, Felix Brunner, Nils Berger
- [ ] Inspect `archive.zip` (`soony/context/onboarding_notes.md`, BOMs, POs, stock, shipments, contracts) to find hidden traps
- [ ] Pick our core wedge & fill in `.brain/GOAL.md` (Core Goal, Must-Have Scope, Non-Goals)
- [ ] Define initial endpoints & types in `API_CONTRACT.ts` + mock payloads
- [ ] Fill in `.brain/DESIGN_SYSTEM.md` and `.brain/DEMO_SCRIPT.md`

## Next

- [ ] **Agent build list → [`.brain/tasks/README.md`](tasks/README.md)**: 8 parts (shared backend, Watcher, Ingest & Normalizer, Reconciler & Gate, Chaser, Launch Planner, Risk Rater, frontend later), each with numbered tasks. Claim a task by adding `— @<you>` next to it in that agent's file.

## Done

- [x] `@aanjneya` Drop START Hack Munich challenge briefs & rubric into `challenge/` (Lio: Keep the Line Running)
- [x] `@aanjneya` Initialize 4-person hackathon repo, `second-brain` skill, `AGENTS.md`, and symlinks
- [x] `@aanjneya` Harden hackathon infra: 3-way `brain-push.sh`, `.githooks/pre-push`, `verify.sh`, `setup.sh`, `@AGENTS.md`, zero-intervention brain sync
