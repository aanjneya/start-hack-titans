# Architecture

> Shared system architecture for **start-hack-titans** (Aanjneya, Daksh, Jivi, Tim).
> Write once at Hour 1–2 and update only on major structural changes.

## System Overview

**Adopted plan: [`PLAN.md`](PLAN.md)** (process, agents, screen, demo case, build order).
We sell a standard, agent-run process that gets every part from order to the line before a hard deadline; Soony's GH6 launch is customer #1.
Loop: Watcher (15-min portal SHA diff) → Ingest & Normalizer → Reconciler & Gate → Chaser / Launch Planner & Decision Copilot → one-click human approval → execute → next drop confirms or reopens. Risk Rater feeds checks and plans; a Coordinator logs every step to the AI-traceability drawer.

## Tech Stack

- **Frontend**: Next.js + TypeScript in `web/` (phase 2, after the agents)
- **Backend / API**: Python 3.12+, FastAPI (`backend/app/main.py`), agents in `backend/agents/`, event bus `backend/core/coordinator.py`
- **Database / Storage**: SQLite via SQLModel (`backend/data/truth.db`, gitignored), append-only facts + `part_journey` view (PLAN.md §3b)
- **External APIs / AI**: Claude API via `backend/core/llm.py` (default `claude-opus-5-5`), Soony portal API (Watcher). `MOCK_MODE=true` = fully offline
- **Deploy Target**: _TBD_ (web on Vercel; backend on a small Python host)

## Directory Layout

```text
.
├── .agent/skills/second-brain/   # Shared hackathon coordination skill
├── .brain/                       # Auto-synced shared brain (3-file core + optional logs)
├── .claude/settings.json         # Zero-prompt allowlist for brain-push.sh & verify.sh
├── .githooks/pre-push            # Mechanical safety: blocks secrets/force-push, verifies main
├── challenge/                    # START Hack Munich briefs, rubrics, sponsor docs (<25MB)
├── scripts/
│   ├── brain-push.sh             # 3-way merge worktree sync (.brain/ -> origin/main)
│   ├── verify.sh                 # Stack-agnostic verification (env/secrets/Node/Python)
│   └── setup.sh                  # 60-second teammate onboarding & OS symlink check
├── AGENTS.md                     # Universal agent rules (imported by CLAUDE.md via @AGENTS.md)
├── backend/                      # Python agents + FastAPI (see backend/README.md)
├── API_CONTRACT.ts               # Shared TypeScript API contract (mirrors backend/core/models.py)
├── .env.example                  # Required environment variables (MOCK_MODE=true)
└── README.md                     # 60-second onboarding & workflow quickstart
```

## Service Boundaries & Data Flow

<!-- Document key modules, endpoints, and how data moves between them. -->
