# Project Goal & Alignment

> **Living North Star for `aanjneya`, `daksh`, `jivi`, `tim` (synced regularly to `main` with `.brain/`).**
> Every agent MUST read this file before starting work and keep it aligned with where the team is heading.

## Agent Protection Rule (READ CAREFULLY)
- **Never overwrite or delete** existing items in this file unless the team is intentionally and completely changing that specific thing.
- **Never edit `.brain/GOAL.md` silently or bundled with code changes** (so a teammate never clicks "Allow" without noticing).
- Whenever `.brain/GOAL.md` should be changed, refined, or pivoted — or if a prompt contradicts it:
  1. **STOP** before editing `.brain/GOAL.md`.
  2. Send a **separate, dedicated chat message** starting with `⚠️ GOAL CHANGE PROPOSAL — EXPLICIT APPROVAL REQUIRED` showing the exact **BEFORE → AFTER** diff and a 1-line **Why**.
  3. Wait for the user's **explicit chat reply** approving the change before modifying `.brain/GOAL.md` and running `./scripts/brain-push.sh`.

---

## 1. Core Goal (What We Are Building)

- **Problem**: Companies with a hard launch date only have a system of record for 2 of 7 supply steps (Order, Pack). Confirmations, shipments, tracking and receiving live in multilingual PDFs, Excel and phone calls, so problems surface "at the dock". Customer #1: Soony must pack every Grand Heist 6 copy in Hamburg by Sun 22 Nov 20:00 (Lio challenge "Keep the Line Running").
- **Solution (1–2 sentences)**: We sell a standard, agent-run process that gets every part from order to line before a hard deadline: watch → normalize → check → chase → decide (one-click human approval, € cost vs copies gained) → act & confirm. Soony is the first setup; any company plugs in its own data, deadline, rates and approval rules, with no new code.
- **Target User**: Head of Procurement / CPO (decides on one page), buyers (stop chasing by phone), IT lead (audits the agents via the trace).
- **Why It Wins (Judge Hook)**: Live on real portal data, a new drop arrives, agents catch the CMP-GAM-004 revision trap, chase Karton Nowak in Polish, propose the express rev C order, Maren approves in one click, the pack forecast turns green, and the trace shows why. Real agent actions, nothing hardcoded, works beyond Soony.

## 2. Must-Have Scope (For the Demo)

1. Ingest & Normalizer + Reconciler on real portal data (ERP CSVs, both PO formats, tracker xlsx, BOM/ECN, launch_status), surviving new drops and schema changes.
2. Launch Planner: hour-by-hour pack forecast (Collector's first), Plan A/B/C decision cards with € vs copies and the correct PP-07 approver; one-click approve updates the forecast.
3. One screen ("what can stop a line") + ⚡ AI-traceability drawer + LIVE/MOCK toggle; Chaser sends at least one real, rate-limited supplier email.

## 3. Explicit Non-Goals (Do NOT Build)

- No auth/login, roles or admin settings; no multi-tenant onboarding UI (show customer setup on one slide).
- No OCR of all 61 dock scans (2–3 for the demo at most); no supplier scout / second-source search (pitch only).
- No forty dashboards: one page plus the trace drawer.
- No unthrottled supplier emails (suppliers stop answering if spammed).

## 4. Direction & Alignment Log

| Time | By | Direction Update / Alignment Note |
| :--- | :--- | :--- |
| Init | `aanjneya` | Initialized living goal & alignment file for START Hack Munich. |
| Sat 17:45 | `daksh` | Filled Core Goal, Must-Have Scope and Non-Goals from the adopted `.brain/PLAN.md` (team-approved in chat: "Yes, update GOAL"). |
