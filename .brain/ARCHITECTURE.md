# Architecture

> Shared system architecture for **start-hack-titans** (Aanjneya, Daksh, Jivi, Tim).
> Write once at Hour 1–2 and update only on major structural changes.

## System Overview

<!-- Brief summary of what we are building and the end-to-end demo flow. -->

## Tech Stack

- **Frontend**: _TBD_
- **Backend / API**: _TBD_
- **Database / Storage**: _TBD_
- **External APIs / AI**: _TBD_ (must support `MOCK_MODE=true` env var + runtime `?mock=1`/hotkey toggle)
- **Deploy Target**: _TBD (auto-deploy from `main` by Hour 1.5)_

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
├── API_CONTRACT.ts               # Shared TypeScript API contract
├── .env.example                  # Required environment variables (MOCK_MODE=true)
└── README.md                     # 60-second onboarding & workflow quickstart
```

## Service Boundaries & Data Flow

<!-- Document key modules, endpoints, and how data moves between them. -->
