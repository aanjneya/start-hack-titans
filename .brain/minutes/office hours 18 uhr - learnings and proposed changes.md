# Office hours Sat ~18:00: Soony head of procurement (+ Lio) · learnings

> Raw transcript: [`office hours 18 uhr soony head of procurement.txt`](office%20hours%2018%20uhr%20soony%20head%20of%20procurement.txt) (Speaker A = Soony head of procurement persona, later answering as Lio; B/C/D = us; Felix was absent).
> Written by daksh's agent. **Nothing in PLAN.md, GOAL.md or the task files was changed**: section 3 lists proposed changes for the team to discuss first.

## 1. What Soony (head of procurement) told us
1. **Actions, not dashboards.** "I don't care about those dashboards where you don't get anything. I need actions." "I need the risk, but I want the solution." The dashboard is nice, but the real value is in the action.
2. **Risk → reason → solution, always together.** Name the risk, show why, offer a concrete fix (e.g. "get a second supplier"). The reason must be visible: an **executive summary first, full detail on demand** for colleagues who want to read more.
3. **Traceability matters for trust and blame.** Other colleagues fear AI "hallucinating"; if an agent keeps making mistakes, "we would need to know who did the mistakes."
4. **Human in the loop = a switch.** Still to be decided with their boss: they want to switch agents to "full mode" (autonomous) or back to "control" (approval limits). It must be configurable, not built in one way.
5. **Approval limits (exact numbers):**

   | Amount | Approver |
   |---|---|
   | < €50k | Buyer |
   | < €500k | Category lead |
   | < €2M | Head of procurement |
   | < €5M | CPO |
   | ≥ €5M | CPO + CFO |
6. **Roles see different things.** "You always have different roles… those other people don't see what I see."
7. **Long-term, fair fixes.** A new supplier must be a long-term relationship, not a forced one-off.
8. **Past pain:** single-source risk "was horrible because we only got to know about it too late."

## 2. What Lio told us (judging and demo)
1. **Our demo flow looked too sequential.** In reality things run **in parallel**: onboarding a supplier, a truck with a flat tyre and a news risk happen at the same time. Don't show a step-by-step pipeline.
2. **Nothing stood out as unique yet.** "Nothing where I was like, oh my gosh." We need one memorable thing.
3. **Long-term over saving the launch is acceptable.** If a generally useful product makes the pitch/business case stronger, that can beat "saving the launch"; then phrase the pitch that way.
4. **Deployability counts, but isn't make-or-break.** "Step 1 works, step 2 mostly, step 3 hardcoded, here's the vision" can win if the problem → solution story is clear.
5. **Don't show code** in the pitch (people fall asleep), but "one click and everything is solved" without showing what happened also fails. Show what the agents did.
6. Lio is at START Hack for branding and hiring; solving Lio's own product problems is not the goal.

## 3. Proposed changes (NOT applied: discuss and approve first)
| # | Where | Proposal | Why |
|---|---|---|---|
| P1 | PLAN.md §3 UI, tasks/7 | Lead with an **action inbox** (decision cards), not charts; every risk shows **risk → reason → recommended action** | "I need actions" |
| P2 | Decision card (tasks/5 T5.7, API_CONTRACT) | Add `summary` (one line, exec) + expandable `reasoning` + `evidence` | exec summary + detail on demand |
| P3 | tasks/5 T5.6 | Use the **approval matrix above** (from the source, not a guess) to pick the approver; cross-check with `approval_policy_PP-07.pdf` | real numbers from Soony |
| P4 | Planner + shared backend | **Autonomy switch**: per amount threshold, agents either execute directly ("full mode") or wait for approval ("control"); default = control | configurable HITL |
| P5 | GOAL.md non-goals vs roles | GOAL says "no roles"; Soony wants role views. Suggest a **role picker** (Buyer / Head of procurement / CPO) that only changes which cards and approval buttons show, with no login | role-based without auth scope creep |
| P6 | Risk Rater (tasks/6) | Every risk grade **must come with a suggested action** (2nd source, safety stock, expedite) | "don't tell me the risk, tell me what to do" |
| P7 | Pitch/UI | Show agents **working in parallel** (live activity lanes per agent), not a linear pipeline | Lio: "too sequential" |
| P8 | Pitch | Find the **one standout**: candidate = "magnet export ban → agent spots single-source risk, proposes 2nd source + safety stock, routed to the right approver by amount, executed in one click, fully traced" | nothing stood out yet |
| P9 | Agent log / trace | Make "**which agent made which mistake**" easy: per-agent error and override counts in the trace | blame/trust for colleagues |
