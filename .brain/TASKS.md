# Live Task Board

> Lightweight shared board synced via `.brain/` on `main` (replaces separate lock files).
> Format: `[ ]` todo, `[~] @<id> Task (active/folder/, shared-hub-file)` in progress, `[x] @<id>` done.
> **Collision check**: Always list active folders and shared hub files (`API_CONTRACT.ts`, `package.json`, root router/layout, DB schema) in parentheses on your `[~]` task. Warn the user before editing a path listed on another teammate's active `[~]` task.

## Now (Active Sprint)

- [x] @daksh 1 Watcher (`backend/agents/watcher.py`) ✅ Sat 19:35
- [x] @daksh 2 Ingest & Normalizer (`backend/agents/ingest*.py`) ✅ Sat 19:35 · OCR 19:50 · **live-tested 21:15 with OpenAI** (60/60 scans, 0 errors) · handles the 21:38 drop (stock date from sheet title) · 63 tests green on `daksh` (22:10)
- [x] @aanjneya 3 Reconciler & Gate (`backend/agents/reconciler.py`, `backend/agents/reconciler_rules.py`, `backend/tests/test_reconciler.py`, `.brain/tasks/3-reconciler-gate.md`) ✅ Sat 20:38 · Finding Format v1, 3-drop replay green (34 tests)
- [~] @tim 4 Chaser: built + tested on branch `tim` (merge to main waits for daksh's OK); open: first real mail to Karton Nowak (`backend/agents/chaser.py`, `backend/core/mail.py`, `backend/core/config.py`, `backend/core/models.py`, `backend/app/main.py`, `API_CONTRACT.ts`)
- [x] @jivi Risk-to-decision buyer flow on jivi only. The buyer sees market provenance, supplier evidence, dated sample comparisons, assigned reviews and priced freight options. The sample inquiry and PP-07 serial release have separate authority. See Task 5 and the branch demo walkthrough.
- [x] @jivi The buyer’s decision now shows the supplier, part and product grades, source passages, current shipment disagreements and the line amounts behind the approval. Startup checks the committed action against current evidence before dispatch. Verified on jivi with 95 backend tests and the mobile buyer walkthrough.
- [x] @jivi The qualification card shows the contributing market, trade news, regulatory, route weather and shipment sources. The pickup shows its own tracking record. Source dates, scope and score caps are readable under Why.
- [x] @jivi Decision and reply failures show their agent and trace. The order loop supports customer-defined references and split shipments. The buyer page builds offline with its existing Archivo font.
- [x] @jivi A changed supplier grade opens its buyer decision directly. Accepted deliveries retain their measured forecast outcomes across later drops. Current buyer reads and trace links were verified with the 104-test backend suite.
- [x] @jivi 5 Launch Planner & Decision Copilot. A buyer can trace a grade change to supplier history and dated orders, compare sample timing, start a qualification review and choose updates. Independent public-feed refresh and one six-agent portal trace verified. The €500 pickup simulation verifies financial authority and the confirmed packing gain. [Task 5](tasks/5-launch-planner.md)
- [x] @aanjneya 6 Risk Rater (`backend/agents/risk.py`, `backend/agents/risk_signals.py`, `backend/agents/risk_scoring.py`, `backend/tests/test_risk.py`, `.brain/tasks/6-risk-rater.md`) ✅ Sat 22:07 · 3-tier Supplier→Part→Product Moody's engine + 5 live global feeds (Polymarket, Open-Meteo, Google News RSS, GPS/AIS Telemetry, Global Regulations: US Federal Register BIS/USTR + EUR-Lex + WTO + dynamic per-country CN/KR/IN/DE/PL/any) + Finding Format v1 (61 tests green)
- [x] @aanjneya 6b Risk-to-Action Playbook + AI Observability (`backend/agents/risk_recourse.py`, `risk_playbook.py`, `risk_scout.py`, `risk_breakers.py`, `backend/app/main.py`, `API_CONTRACT.ts`, `backend/setup/soony.json`, `web/app/risk-playbook.tsx`, `web/app/control-room.tsx`, `web/app/observability/`) ✅ Sun 09:01 on `aanjneya` (`cf462c8`) · Path-to-Upgrade `CCC→A` optimizer, Pre-Emptive Chasing (72h early), Geo-Decorrelated 2nd-Source Scout + RFQ pack, Risk-Triggered Circuit Breakers · **114/114 backend tests + `./scripts/verify.sh` green**
- [~] @daksh 7 Frontend, basic screen (`web/`, Next.js 16 + Tailwind, `.brain/tasks/7-frontend-later.md`) on `daksh` 22:55: forecast per edition, decision cards with Approve/Why?, agent lanes, live log, findings/emails/risk tabs, test reply box; run steps in `web/README.md`. Plus `backend/tests/test_e2e_loop.py` (whole loop through the API). Open: T7.1 pipeline bar, T7.5 trace per card, T7.6 part journey, deploy. · Sun 02:40: control room "Risks to act on" = one tile per part/supplier → dialog with the 3 best actions as 3D cards, each routed to its approver by € (Soony matrix, demo seat approves); AI Observability: agent roster, Watcher/Ingest workspace (sheet + agent chat with live step card), green/white theme (`web/app/risk-board.tsx`, `web/app/observability/workspace/`, `web/components/ui/`, `web/package.json`: lucide-react, framer-motion, clsx, tailwind-merge, tw-animate-css) · Night to 06:00: numbers-first tiles with plain titles, tiles read jivi's `GET /api/buyer-queue` when present (version on Approve, refresh on 409), approval-matrix chip, AI Observability phone rail + plain wording · Sun 10:00 merged to main: risk tiles in front with Jivi's buyer-queue views behind "More details", Sage Garden colours, hand-drawn titles, packing forecast charts on AI Observability (`web/app/risk-board.tsx`, `web/app/observability/forecast-chart.tsx`, `web/components/ui/`)
- Shared hubs (change with care, tiny commit straight to main): `backend/core/models.py`, `backend/core/events.py`, `API_CONTRACT.ts`

- [ ] Register team on Lio Customer Portal (`start-hack-portal-cxh3crbdaef2f8gt.westeurope-01.azurewebsites.net`), grab API token, download `/api/v1/archive.zip`, and register sender emails
- [ ] Book Office Hours (Sat 14:00 / Sat 17:00 / Sun 09:00) with Dr. Maren Vogt, Felix Brunner, Nils Berger
- [ ] Inspect `archive.zip` (`soony/context/onboarding_notes.md`, BOMs, POs, stock, shipments, contracts) to find hidden traps
- [ ] Pick our core wedge & fill in `.brain/GOAL.md` (Core Goal, Must-Have Scope, Non-Goals)
- [ ] Define initial endpoints & types in `API_CONTRACT.ts` + mock payloads
- [ ] Fill in `.brain/DESIGN_SYSTEM.md` and `.brain/DEMO_SCRIPT.md`

## Next

- [ ] **Agent build list → [`.brain/tasks/README.md`](tasks/README.md)**: 8 parts (shared backend, Watcher, Ingest & Normalizer, Reconciler & Gate, Chaser, Launch Planner, Risk Rater, frontend later), each with numbered tasks. Claim a task by adding `— @<you>` next to it in that agent's file.

## Done

- [x] `@daksh` Shared backend (T0.1–T0.8): `backend/` FastAPI + SQLite truth store + event bus + agent log + Claude helper + replay of real drops; all agents exist as stubs (`backend/README.md`)

- [x] `@aanjneya` Drop START Hack Munich challenge briefs & rubric into `challenge/` (Lio: Keep the Line Running)
- [x] `@aanjneya` Initialize 4-person hackathon repo, `second-brain` skill, `AGENTS.md`, and symlinks
- [x] `@aanjneya` Harden hackathon infra: 3-way `brain-push.sh`, `.githooks/pre-push`, `verify.sh`, `setup.sh`, `@AGENTS.md`, zero-intervention brain sync
