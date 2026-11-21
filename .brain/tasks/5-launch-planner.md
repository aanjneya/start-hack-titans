# 5 · Launch Planner & Decision Copilot

**Owner:** @jivi (T5.0–T5.10 active on branch `jivi`)

## What this agent does
It answers the one question that matters: **"Will we pack every copy by Sunday 20:00, and if not, what do we do?"** It forecasts packing hour by hour, finds which missing part stops which line, and proposes 2–3 fixes with their **cost in € vs copies saved**. It sends each proposal to the right person to approve, and after approval it carries the fix out and checks it worked.

## Why we need it
The CPO memo: "One page per day on what can stop a line. Not forty dashboards." and "air freight and rush orders above the policy limits reach me the same hour, with cost against copies gained." This agent produces exactly that. It's the "clear recommendation and one click to act" the judges reward.

## Example from our real data
`launch_status.xlsx` (Sat 13:00): Standard 86,000 left at 6,000/h (about 14 h); Collector's 18,900 left at 900/h (**about 21 h of about 23 h left**). Collector's goes first (memo). If rev B sleeves are blocked, the agent shows: "Option A: Karton Nowak express rev C, € X, +Y copies, approver Maren. Option B: …".

## What it works with
- **Gets:** "copies at risk" issues from the **Reconciler**; stock, inbound shipments and BOM from the database; risk grades from the **Risk Rater**.
- **Gives:** the pack forecast + decision cards → human (and later the frontend); after approval, actions → **Chaser** (emails) and the database.

## Tasks
- [ ] **T5.0** Input = Findings (`.brain/DATA_FORMAT.md`, `kind=copies_at_risk`, plus risk grades); output = DecisionCards. Use the finding's `impact` and `suggested_actions` as the starting options.
- [ ] **T5.1** Forecast packing per edition: copies left, packing rate, shift hours left (Sat 06–22, Sun 06–20), read from the files.
- [ ] **T5.2** Turn copies into parts needed (BOM: e.g. 4 magnets per Collector's box) and compare with usable stock + shipments arriving in time.
- [ ] **T5.3** Apply the priority rules from the CPO memo: Collector's first, nothing packed without a quality release.
- [ ] **T5.4** Generate fix options: pick up at the port now, switch sea → air, the supplier's express offer, move shared parts between editions, use up old revisions where allowed.
- [ ] **T5.5** Price each option: € cost (from contracts, offers, routes) vs copies saved.
- [ ] **T5.6** Find the right approver with the customer's **approval matrix** in `backend/setup/<customer>.json` (Soony, from office hours: Buyer <€50k · Category lead <€500k · Head of procurement <€2M · CPO <€5M · CPO+CFO ≥€5M; helper `core/policy.py: approver_for(amount)`). Cross-check people's names with `org_chart_v2.pdf`.
- [ ] **T5.7** Build the decision card as **risk → reason → recommended action**: one-line `summary` (for the exec), options, recommendation, approver, decide-by time, and full `reasoning` + evidence for anyone who wants detail.
- [ ] **T5.10** Autonomy switch: if the setup says `auto` and the option's cost is below `auto_execute_below_eur`, execute directly and log it; otherwise (`control`, default) wait for approval (`core/policy.py: needs_approval(amount)`).
- [ ] **T5.8** After approval: trigger the action (email via Chaser, record the change), recalculate the forecast, and later mark the issue solved or reopen it.
- [ ] **T5.9** Live data: recalculate the forecast and cards after each drop (e.g. a new `launch_status` file), and mark cards outdated if their numbers changed.

## When it's done
- Hero case works through the API: rev B issue → card "express rev C, € X, +Y copies, approver Maren" → approve → forecast improves → log shows the whole chain.
- Changing a number in the data changes the forecast (nothing hardcoded).

---
### Technical notes (for Claude Code, refine later)
- Inputs: `soony/context/launch_status.xlsx`, `soony/master/bom.csv` + ECN rows, `stock`, `shipments` (eta + dock lead time), `soony/context/memo_launch_priorities.docx`, `soony/context/approval_policy_PP-07.pdf`, `soony/context/org_chart_v2.pdf`, `suppliers/SUP-0028_Karton_Nowak/notices/Oferta_tryb_ekspresowy_2026-11-21.pdf`.
- Forecast = deterministic Python (testable); option generation and wording via `claude-opus-5-5` with tool calls into the forecast function. DecisionCard: `issue_ids, options[{label, cost_eur, copies_gained, actions[]}], recommended, approver, decide_by, status`.
- Emits `decision.created`; on `decision.approved` → actions + `forecast.updated`.
