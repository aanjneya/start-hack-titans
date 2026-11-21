---
name: second-brain
description: Active 4-person hackathon brain — automatic zero-intervention .brain/ sync, strategic analysis, high-signal pushback, lean 2-tier context loading, living goal alignment, and cross-agent handoffs. Winning START Hack Munich is a must. Always run before starting work, after fixing bugs, and before pushing.
---

# Second Brain Protocol

Shared active brain & coordination protocol for the 4-person **START Hack Munich** team:
- **Teammate IDs (`<YOUR_ID>`)**: `aanjneya`, `daksh`, `jivi`, `tim` (auto-detected from `git config --get hackathon.teammate` or branch name — never ask the user).
- **Mission**: **Winning START Hack Munich is a must.** Analyze context actively, connect dots across teammates, warn when something is wrong or low-ROI, and recommend stronger, faster paths.
- **Zero-Human-Intervention Rule**: Update `.brain/` and run `./scripts/brain-push.sh` **automatically** at natural checkpoints without asking permission or waiting for the user to prompt you. (Only `.brain/GOAL.md` requires explicit user approval.)

> `./scripts/brain-push.sh` uses a temporary worktree + 3-way `git merge-file` so it **never** touches WIP feature code and **never** silently overwrites teammates' edits.
> **Command shape**: Always invoke `./scripts/brain-push.sh "<short summary>"` as a **standalone command** (no `&&`, `;`, or `$()`) so teammates' one-time approval runs with zero prompts.

## 1. Before Starting a Session (Auto-Setup + Lean 2-Tier Context Load)

0. **Auto-setup fresh clones (zero manual onboarding)**:
   - Run `./scripts/setup.sh --check`. If it exits non-zero (fresh clone or missing hooks/ID), run `./scripts/setup.sh` immediately.
   - `./scripts/setup.sh` auto-detects the teammate ID (`aanjneya|daksh|jivi|tim`) from git config/user/email/OS username, enables `.githooks`, verifies skill symlinks, creates `.env`, and switches to their personal branch.
   - If it exits `2` (could not auto-detect ID), ask the user *"Which teammate are you: `aanjneya`, `daksh`, `jivi`, or `tim`?"* and run `./scripts/setup.sh <id>` right away.
1. **Sync latest team state & check freshness**:
   - Run `git fetch origin` and check `.brain/` freshness via `git log -1 --format=%cr origin/main -- .brain/`.
   - If `.brain/` hasn't been updated in **>3 hours**, warn the user: *"Heads up: `.brain/` is >3h old — confirm recent verbal decisions at the table before relying on `TASKS.md`."*
   - Merge `origin/main` into your branch when working tree is clean (`git merge origin/main --no-edit`).
2. **Tier 1 — Always Read (Fast, 3 tiny files + handoff check)**:
   - `.brain/GOAL.md` (living North Star, non-goals, direction log)
   - `.brain/TASKS.md` (who is working on what right now)
   - `.brain/INSIGHTS.md` (active warnings, rubric gaps, winning ideas)
   - Check `.brain/handoffs/` for `*-to-<YOUR_ID>-*.md` — read and complete matching handoffs first, then move them to `.brain/handoffs/done/`.
3. **Tier 2 — Read On-Demand Only When Relevant (Never Guess!)**:
   - `challenge/` → rubric weights, sponsor APIs/datasets.
   - `.brain/ARCHITECTURE.md` & `API_CONTRACT.ts` → routes, services, shared types.
   - `.brain/DESIGN_SYSTEM.md` → UI/frontend work.
   - `.brain/DEMO_SCRIPT.md` → demo beats, copy, wow-factor.
   - `.brain/DECISIONS.md` → architectural trade-offs.
   - `.brain/gotchas/*.md` → errors, SDK quirks, unfamiliar modules.
4. **Think like a brain (Active Analysis & High-Signal Pushback)**:
   - Evaluate major requests against the **5-Point Winning Lens**: (1) judging rubric & sponsor fit, (2) 3-minute live demo wow-factor, (3) hackathon time-to-ship ROI, (4) `MOCK_MODE=true` reliability, (5) cross-team coherence.
   - **Routine/small tasks**: Execute immediately without chatter.
   - **Suboptimal or risky requests**: Proactively warn and propose a stronger/faster path.
   - **Protected Goal Alignment (`.brain/GOAL.md`)**:
     - **Never edit `.brain/GOAL.md` silently or bundled with code edits.** Whenever a prompt implies changing, refining, or pivoting `.brain/GOAL.md` (or contradicts it):
       1. **STOP** before editing `.brain/GOAL.md` or writing conflicting code.
       2. Send a dedicated message starting with `⚠️ GOAL CHANGE PROPOSAL — EXPLICIT APPROVAL REQUIRED`, showing the exact **BEFORE → AFTER** diff and a 1-line **Why**.
       3. Only edit `.brain/GOAL.md` (plus its Direction Log) and run `./scripts/brain-push.sh` **after** the user explicitly replies in chat approving it.
5. **Claim your slice automatically in `.brain/TASKS.md`**:
   - Check active `[~]` lines first — warn the user if another teammate lists a folder or shared hub (`API_CONTRACT.ts`, `package.json`, root layout/router, DB schema) you need to edit.
   - Add your active slice with touched paths in parentheses — e.g. `[~] @<YOUR_ID> Build intake API (api/intake/, API_CONTRACT.ts)` — and immediately run `./scripts/brain-push.sh "Claim <slice>"` without asking.

## 2. During Work: 3-File Core & High-Signal Updates Only

- **3-File Living Core (Kill Doc Tax)**:
  - **Keep live**: `GOAL.md` (pivots only), `TASKS.md` (slice start/finish), `DEMO_SCRIPT.md` (when demo beats change).
  - **Write once at Hour 1–2**: `ARCHITECTURE.md`, `DESIGN_SYSTEM.md`.
  - **Append-only when it saves a teammate >15 min**: `INSIGHTS.md`, `DECISIONS.md`, `gotchas/<YOUR_ID>.md`, `handoffs/`. Keep every `.brain/*.md` under **~100 lines**.
- **Bug or SDK quirk (>15 min trap)**: Append a 2-line `Issue` + `Fix` entry **strictly** to `.brain/gotchas/<YOUR_ID>.md` and auto-sync.
- **Strategic warning / rubric gap / winning idea**: Append a concise bullet to `.brain/INSIGHTS.md` (`[x]` when fixed) and auto-sync.
- **Rolling merge discipline**: Keep slices small and merge to `main` every **2–3 hours**. Land shared hub changes (`API_CONTRACT.ts`, `package.json`) on `main` immediately so teammates pull them right away.

## 3. Before Pushing Code or Handing Off Work

1. **Always verify before pushing code**:
   - Run `./scripts/verify.sh` before pushing a feature branch or merging into `main` (also enforced automatically by `.githooks/pre-push` on `main`).
2. **Auto-update brain state (0 human prompts)**:
   - Mark your slice `[x] @<YOUR_ID>` in `.brain/TASKS.md` and mark fixed warnings `[x]` in `.brain/INSIGHTS.md`.
   - If handing off to a teammate, write `.brain/handoffs/<HHMM>-<FROM>-to-<TO>-<topic>.md` with: branch name, ready endpoints/components/types (`API_CONTRACT.ts`), new env vars (confirmed in `.env.example`), and exact next prompt for the receiving agent.
3. **Auto-push `.brain/` (or `challenge/`) to `main`**:
   ```bash
   ./scripts/brain-push.sh "<short summary>"
   ```
   Include `--include-challenge` whenever adding new START Hack Munich briefs/docs (<25MB) to `challenge/`.
