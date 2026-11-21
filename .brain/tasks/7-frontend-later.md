# 7 · Frontend (phase 2, after the agents work)

**Owner:** phase 2 (start when M2 is reached, ~Sat 22:30)

## What this is
The one screen the Soony team and the judges see. It shows only what can stop a line and what to decide, with one click to approve. Everything technical sits in a side drawer that shows what each agent did and why.

## Why we need it
Judging "Taste": "Does it look and feel like a real product? … a clear recommendation and one click to act." CPO memo: "One page … Not forty dashboards." The trace drawer is our AI-traceability must-have.

## What it works with
- **Gets:** the backend API (forecast, issues, decision cards, risk grades, agent log, outbox).
- **Gives:** approve/reject clicks → **Launch Planner**.

## Tasks
- [ ] **T7.1** Top bar: countdown to Sun 20:00, the 7-step pipeline status (Order → Confirm → Ship → Track → Receive → Pack → Launch), LIVE/DEMO switch.
- [ ] **T7.2** Main page = **action inbox** "What can stop a line": decision cards first (office hours: "actions, not dashboards"), then the forecast per edition (packed / to pack / expected by 20:00) as a small bar.
- [ ] **T7.3** Decision cards: one-line summary, options with € vs copies, recommended option, approver (from the approval matrix), **Approve** button; "Why?" expands the reasoning and evidence; after approval the forecast updates live.
- [ ] **T7.4** Risk table: parts and suppliers with grade and top 3 reasons.
- [ ] **T7.5** ⚡ AI trace drawer: for any card, show source file → what the agent read → its reasoning → emails sent and received; plus per-agent counts of overrides/errors ("which agent made the mistake").
- [ ] **T7.7** Live agent lanes: small activity strip per agent showing they work **in parallel** (Lio: our flow looked "too sequential"). No role logins (vision only).
- [ ] **T7.6** Second tab "Shipments & suppliers" (Tim's `part_journey` view), then deploy and record the 90-second backup video.

## When it's done
- A judge understands the screen in 10 seconds and can approve a card in one click.
- It still works in demo mode with the Wi-Fi off.

---
### Technical notes (for Claude Code, refine later)
- Next.js (App Router) + TypeScript + Tailwind/shadcn in `web/`; types from `API_CONTRACT.ts`; one design language (`.brain/DESIGN_SYSTEM.md`, still to fill).
- Deploy: Vercel for `web/`; backend on a small Python host. `?mock=1` + a subtle `MOCK` badge per AGENTS.md.
