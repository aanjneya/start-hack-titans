# start-hack-titans

4-person START Hack Munich repository for `aanjneya`, `daksh`, `jivi`, and `tim` — powered by an automatic, zero-intervention **Second Brain** across Antigravity, Claude Code, and Codex CLI.

## 60-Second Onboarding

**Option A — Let your coding agent do it (Recommended):**
```bash
git clone <repo-url>
cd start-hack-titans
```
Open the repo in **Antigravity, Claude Code, or Codex CLI** and start working (or say `"set up the repo for me"`). Your agent automatically runs `./scripts/setup.sh`, detects your teammate ID, enables `.githooks`, verifies skill symlinks, creates `.env`, and switches to your personal branch.

**Option B — Manual one-liner:**
```bash
./scripts/setup.sh          # auto-detects your ID (or pass: aanjneya | daksh | jivi | tim)
```

## Zero-Intervention Second Brain

- **Agents update & push `.brain/` automatically** via `./scripts/brain-push.sh` (using a temporary worktree + 3-way per-file merge — never touches WIP code and never silently overwrites teammates).
- **One-time setup for 0 prompts**:
  - **Claude Code**: Pre-allowed automatically via `.claude/settings.json`.
  - **Antigravity / Codex**: Click **"Always allow `./scripts/brain-push.sh`"** once on first run so all future brain syncs happen silently.
- **Only exception**: `.brain/GOAL.md` requires explicit chat approval before edits so no agent silently pivots scope.

## Core Team Rules

1. **Rolling 2–3h merges to `main`**: Keep personal branches (`aanjneya`, `daksh`, `jivi`, `tim`) short-lived. Merge working vertical slices to `main` every 2–3 hours after `./scripts/verify.sh` passes.
2. **Hub-first commits**: Changes to `API_CONTRACT.ts`, `package.json`, or root router go to `main` immediately so nobody diverges.
3. **Secrets & `.env`**: Share sponsor API keys via the pinned team `.env` note (never commit `.env` — `.githooks/pre-push` blocks it). `./scripts/verify.sh` flags missing keys automatically.
4. **Stage survival (`MOCK_MODE=true`)**: Support both `MOCK_MODE=true` env var and a runtime toggle (`?mock=1` or hotkey with a subtle `MOCK` indicator). Tag `demo-safe` on `main` 60 minutes before submission.
5. **Emergency hook bypass**: If `.githooks/pre-push` ever blocks an urgent pre-demo fix, run `ALLOW_MAIN_PUSH=1 git push origin main`.
