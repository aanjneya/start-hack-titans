# 5 · Launch Planner & Decision Copilot

**Owner:** @jivi (implemented and tested on branch `jivi`)

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
- [x] **T5.0** Input = Findings (`.brain/DATA_FORMAT.md`, `kind=copies_at_risk`, plus risk grades); output = DecisionCards. Use the finding's `impact` and `suggested_actions` as the starting options.
- [x] **T5.1** Forecast packing per edition: copies left, packing rate, shift hours left (Sat 06–22, Sun 06–20), read from the files.
- [x] **T5.2** Turn copies into parts needed (BOM: e.g. 4 magnets per Collector's box) and compare with usable stock + shipments arriving in time.
- [x] **T5.3** Apply the priority rules from the CPO memo: Collector's first, nothing packed without a quality release.
- [x] **T5.4** Generate fix options: pick up at the port now, switch sea → air, the supplier's express offer, move shared parts between editions, use up old revisions where allowed.
- [x] **T5.5** Price each option: € cost (from contracts, offers, routes) vs copies saved.
- [x] **T5.6** Find the right approver with the customer's **approval matrix** in `backend/setup/<customer>.json` (Soony, from office hours: Buyer <€50k · Category lead <€500k · Head of procurement <€2M · CPO <€5M · CPO+CFO ≥€5M; helper `core/policy.py: approver_for(amount)`). Cross-check people's names with `org_chart_v2.pdf`.
- [x] **T5.7** Build the decision card as **risk → reason → recommended action**: one-line `summary` (for the exec), options, recommendation, approver, decide-by time, and full `reasoning` + evidence for anyone who wants detail.
- [x] **T5.10** Autonomy switch: if the setup says `auto` and the option's cost is below `auto_execute_below_eur`, execute directly and log it; otherwise (`control`, default) wait for approval (`core/policy.py: needs_approval(amount)`).
- [x] **T5.8** After approval: trigger the action (email via Chaser, record the change), recalculate the forecast, and later mark the issue solved or reopen it.
- [x] **T5.9** Live data: recalculate the forecast and cards after each drop (e.g. a new `launch_status` file), and mark cards outdated if their numbers changed.

- [x] **T5.11** Rank distinct recovery actions on the buyer screen. Link every contributing finding, group by physical action and approval route, and check the selected version before dispatch.

## When it's done
- Verified hero case on `jivi`: the real rev B Finding compares existing supply with a €1,215 fresh print that arrives too late. Approving the request creates a Chaser handoff. Only confirmed line-ready supply enters the projection. In the mirrored data the Standard bottleneck is game cards, so the simulated sleeve reply adds zero finished copies. The priced recovery regression changes the stock count and verifies a positive gain after confirmation.
- Changing a number in the data changes the forecast (nothing hardcoded).

---
### Technical notes (for Claude Code, refine later)
- Inputs: `soony/context/launch_status.xlsx`, `soony/master/bom.csv` + ECN rows, `stock`, `shipments` (eta + dock lead time), `soony/context/memo_launch_priorities.docx`, `soony/context/approval_policy_PP-07.pdf`, `soony/context/org_chart_v2.pdf`, `suppliers/SUP-0028_Karton_Nowak/notices/Oferta_tryb_ekspresowy_2026-11-21.pdf`.
- Forecast = deterministic Python (testable); option generation and wording via `claude-opus-5-5` with tool calls into the forecast function. DecisionCard: `issue_ids, options[{label, cost_eur, copies_gained, actions[]}], recommended, approver, decide_by, status`.
- Emits `decision.created`; on `decision.approved` → actions + `forecast.updated`.

Run the isolated historical sleeve replay with `backend/.venv/bin/python -m backend.planner_demo`. It distinguishes the site release from the supplier confirmation. In the 17:23 controlled pickup scenario, a €500 fee takes the amended PO above the buyer limit. The category lead authorizes the Chaser to act. A live GMX round trip to the shared test mailbox confirmed 20,000 sleeves and raised Standard from 600,000 to 605,200, leaving 34,800 short. IMAP polling after restart kept one reply. The supplier response and dock slot were simulated. No supplier was contacted.

The evening evidence describes a different baseline. Hamburg projects 608,000 Standard (32,000 short, clear cases) and 66,100 Collector’s (5,900 short, art books). The recorded transcription of WZ-261116-473 is tied to the source file’s SHA-256. The 80,000 booked books include a handwritten correction to 8,000. Lot 5000413071 contributes 16,000 GH6 sleeves after excluding the mislabeled pallet. The remaining game-card shipment is due 24 November. The port pickup and the €16,800 finished-book offer show conditional finished-copy gains. Each still requires site or commercial evidence before the baseline changes.

The buyer control room lists recent approved decisions, the Chaser’s dispatch, the matched reply and the edition forecast at first confirmation. Watcher checked the live 332-file portal manifest twice with no changes. Live Risk Rater checks exercised weather, news, markets and regulation sources alongside shipment telemetry.

### Buyer queue walkthrough

On the evening replay, 19 proposed decisions feed 12 queue entries. The first entry asks the buyer to verify the €16,800 offer for 8,000 finished books. Freight, arrival and dock release determine whether up to 2,500 additional Collector’s copies can be packed. The terminal pickup for SHP-2026-04276 could recover up to 25,200 Standard copies. Its price is unknown. Confirming that same shipment is a separate next step with overlapping gain. Open **Why?** for the source decisions, findings and evidence.

The historical €500 pickup raises the PO approval value to €53,150 and routes to Sofia Marchetti. The isolated mailbox regression verifies one Chaser request after approval and 5,200 additional Standard copies after the matched reply. Replaying the reply adds zero. The full backend suite passed 75 tests, repository verification passed, and the browser walkthrough covered both snapshots.

- [x] **T5.12 (jivi branch)** Route regulatory and second-source human checks into one assigned internal review. The real magnet exposure links the supplier article and lead-time history to the relevant product and open orders. A country-level signal can open an applicability check. A second customer fixture verifies the same behavior with different identifiers, currency, site, timezone and signer.
- [x] **T5.13 (jivi branch)** Isolate replay from the live database, persist the completed commit, and surface interrupted agent work as a partial drop with trace links.

Branch verification: 79 backend tests passed, including the historical pickup and duplicate reply. Repository verification passed. Browser checks covered source evidence, opening the internal review, the failed-drop warning and trace, and Sofia’s €53,150 approval route. The verified demo used an isolated mailbox and historical data.


- [x] **T5.14 (jivi branch)** Bring supplier grade changes, retained external evidence, order-line dates and contract values into one review with a named owner. Corporate order records establish exposure; a market snapshot supplies context. Conflicting article terms route to applicability verification.
- [x] **T5.15 (jivi branch)** Compare competing freight quotes on one line. Each mode can be priced independently. Only one paid service can be authorized. The supplier thread links back to the priced option; a duplicate reply preserves the accepted forecast.

- [x] **T5.16 (jivi branch)** The buyer can compare sample timing against dated open orders, inspect the market question, and follow one decision through its finding, signer, review or supplier thread to the forecast. A market-only warning points to open POs to verify and assigns zero confirmed order impact.

- [x] **T5.17 (jivi branch)** An approved qualification review can independently authorize one sample-terms inquiry through Chaser. A matched reply becomes review evidence for Engineering and Quality. Repeated or mismatched replies do not affect the projection. A paid offer is checked against the live clock again before dispatch.

- [x] **T5.18 (jivi branch)** Match the PO buyer, category and decision date to the customer’s org chart. An unassigned signer requires clarification. PP-07 routes documented exceptions to the head of procurement. The qualification card distinguishes opening a review from serial release, which requires successful PPAP and written approval from Supplier Quality and the responsible Category Lead.

- [x] **T5.19 (jivi branch)** Read the supplier's cited passage, follow the Risk Rater's named supplier, component, assembly and product grades, and compare sample timing directly on the card. Each dated order line remains traceable. An unrelated sample offer cannot authorize an inquiry.
- [x] **T5.20 (jivi branch)** Show both operational shipment readings, the priced PO lines and the exact fee difference between freight options. Opening an internal offer review commits zero. A restart restores an approved dispatch only while its evidence, release and signer still match.
- [x] **T5.21 (jivi branch)** Keep the actual dated sources behind each external supplier score, including both contributing market or political inputs. The buyer can inspect their scope, collection mode and the applied score cap. The pickup card shows only its own shipment observations and identifies shared ERP evidence. A general WTO headline cannot implicate an arbitrary supplier.
- [x] **T5.22 (jivi branch)** Share each PO line's unreceived balance across trucks in arrival order, including scenario overrides. Resolve the buyer on the selected supplier and PO line. A second customer’s alphanumeric order, shipment and revision flow through Chaser and Ingest. Dispatch dates, ambiguous references and replies to drafts are held for review.
- [x] **T5.23 (jivi branch)** Approval and inbox responses report agent failures with trace IDs. The case history retains the affected step. Restart recovers a committed approval and an unprocessed reply on their existing records. The buyer can compare sample request dates with a single click and see material names in the packing forecast.
- [x] **T5.24 (jivi branch)** Take the buyer from the latest supplier grade change to its decision. Link each accepted delivery reply to its actual forecast even after later drops. The market evidence identifies the instrument, side, scope and close where provided. Buyer reads use current records and compact activity while the full history remains available.
- [x] **T5.25 (jivi branch)** Recheck public signals independently of the portal and retain dated observations per source across restarts. A market move can open or withdraw an applicability review. Provider or downstream handler failures surface on the buyer screen with a trace. A sector-specific notice needs a match to this customer's material and supplier records.
- [x] **T5.26 (jivi branch)** Compare the supplier's warning with its actual delivery record. Four completed PackRight box lines met their dates before the December lead time rose from 63 to 105 days. The card links the underlying lines, sample window and assigned reviewer. A customer's launch memo explains edition priority on the immediate recovery card.
- [x] **T5.27 (jivi branch)** Let the demo buyer choose updates about their approvals, decisions and outcomes, or agent activity. The preference is local to their customer and seat. Following an update opens the exact action or supplier thread. Company approval limits still govern dispatch.
- [x] **T5.28 (jivi branch)** Trace Watcher, Ingest, Reconciler, Risk Rater, Planner and Chaser on one drop ID. An authenticated read of the Soony portal from the pre-evening state added 14 files. The repeated manifest contained zero changes, and the evening projection remained 608,000 Standard and 66,100 Collector's. Regression email uses the isolated mailbox.
- [x] **T5.29 (jivi branch)** Reproduce a market-only decision in a second customer's buyer screen with `--serve-market`. The question and its Yes price are visible before the buyer checks for updates. A partial feed check can still use healthy sources alongside a previous reading. The live public-feed run separately returned eight Polymarket observations, one weather reading, one news reading and five regulatory readings, alongside one customer shipment observation.
- [x] **T5.30 (jivi branch)** Accept a named receiving site in a supplier confirmation only when it matches the shipment and customer site master. The browser pickup test used “PO 4500318775 line 2” and “arrival at Hamburg”, confirmed the 5,200-copy change and retained a duplicate with zero additional gain.
- [x] **T5.31 (jivi branch)** Start the real historical warning, the priced pickup, or the market-only case from the demo CLI. The buyer sees a supplier grade change or a pending quote and can open the affected decision directly. Buyer times follow the customer’s time zone. Completed-line history excludes partial receipts and unknown statuses. All 112 backend tests, repository verification and the production browser walkthrough passed.
