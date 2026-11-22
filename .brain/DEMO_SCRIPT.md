# Demo Script (2–3 Minutes)

> Write the exact judge demo flow early. Every feature we build should map directly to a beat below — cut anything that doesn't appear in the demo.

## Opening (0:00–0:30)

“Procurement told us they learned about single-source exposure too late. A new export rule just hit their supplier. Which orders are exposed, and what can the buyer do today?”

## Live demo (0:30–2:15)

Start the API with `backend/.venv/bin/python -m backend.planner_demo --serve-soony --port 8000` and open the buyer screen connected to it. This prepares the first two real drops in a temporary database.

1. Prepare an isolated replay through drop two. On stage, advance the 17:23 drop. **Packing deadline** and **Future supply** appear side by side. Open **Why?** on the qualification card. PackRight moves from A to CCC. The replay’s Polymarket example contributes 11.2 points before and after. Supplier status and trade news change. Show which evidence prompted a closer look and which evidence connects the warning to the customer.
2. Show PackRight’s notice and the three matching PO lines. They cover 73,000 ordered boxes worth CNY 1,870,260 at their recorded prices, with one delivery in December and two in January. These are order values. The review authorizes €0. Under **Compare sample timing**, requesting samples at the historical snapshot leaves 13 calendar days before the first scheduled PO. Waiting two weeks puts the sample date one day after that delivery. Qualification, transit and production lead times are still open. Click **Start qualification review**, open Paul’s assigned review, and show its **Decision history** with named actors and trace records. Existing boxes already contain magnets. To show the handoff, expand **Open assigned review**, preview the request, and choose **Send sample inquiry**. Chaser asks for terms; a threaded test reply goes to Engineering and Quality as review evidence. The weekend projection stays fixed.
3. Keep the pre-priced pickup simulation ready for questions about financial control. Open **Review priced offer** in **Decision follow-up**. The €500 fee brings the amended PO to €53,150 and routes to Sofia Marchetti. Approval authorizes one Chaser request. A matched confirmation adds 5,200 Standard copies under the simulated recount and dock release. The full interaction starts with `--serve-inquiry`; `--serve-pickup` starts with the quote received.


**Evening replay.** At the 21:38 drop Hamburg projects 608,000 Standard (32,000 short, clear cases) and 66,100 Collector’s (5,900 short, art books). Treat the terminal pickup and book offer as conditional recoveries.

**Why the buyer acts before a delivery is late.** Four of PackRight’s four recorded box orders reached Hamburg on time. Open the historical PO lines on the card, then show the supplier's dated notice raising lead time from 63 to 105 days. Switch the sample comparison from today to two weeks to show the date cross the first open PO. That comparison and opening the review fit the main demonstration. Keep the finance simulation as a second tab. Use **My updates** if asked how much an individual buyer hears about. Company policy still decides who signs.

**If asked whether a market can drive a decision itself**, use the separate `--serve-market` demo. Press **Check signals** and follow the grade change to Rei Ito's action for KIT-9/2. That Canadian customer's records stay fixed while a simulated price moves from 20% to 85%. A separate live public-feed smoke check used Polymarket, weather, news, shipment telemetry and regulatory readings and opened an applicability review for a synthetic customer. Keep the Soony story as the main three-minute demonstration.

**Evidence the presenter can open.** In the magnet case, select **Read the supplier’s delivery notice** under **Why?**. The same card shows the historical grade chain from PackRight (A → CCC) through the magnet (AA → CCC) to Collector’s Edition (BBB → CCC). The ordered box itself moves from AA to B. **Compare sample timing** is available beside the primary action. Under the evening terminal review, the ERP records a discharge while the tracker still reports transit. Contacting the terminal is the next useful check.

## 3. Backup Plan
- **Layer 1 (Primary)**: Use the verified `jivi` buyer screen with the dedicated demo database.
- **Layer 2 (Wi-Fi / API drop)**: Open the prepared local replay backend with `MOCK_MODE=true`.
- **Layer 3 (Total fallback)**: Record a 60–90s backup screen recording.

## 4. Risk-to-Action Playbook Beat (15s Wow Moment + Q&A Weapons, on `aanjneya`)
> **Framing (Sun 02:16 clarification):** Soony is Customer #1 — everything below reads dynamically from `backend/setup/<customer>.json`, SQLite BOM/POs, and live global feeds so any manufacturer (BMW, Siemens, ZEISS) gets the exact same engine on Monday morning.

- **15-Second Stage Hero — Moody's Path-to-Upgrade (`CCC → A`):** Open the **Risk** tab (or `/observability?agent=risk`). Show `FG-GH6-CE` rated **`CCC`** — *"Anyone can slap a red risk badge on a launch. Soony's Head of Procurement told us: 'I need the risk, but I want the solution.' So we inverted our Moody's engine into a live **Path-to-Upgrade Optimizer**."* Toggle the top €-ranked levers and watch Collector's Edition jump live from **`CCC` → `BBB` → `A`** in real time, then click **Execute Upgrade** to dispatch linked live decisions.
- **Q&A Weapon #1 — "Why wire Polymarket / Open-Meteo / global regulations?" (Pre-Emptive Chasing):** Point to **Predictive Pre-Emptive Chasing** — live external feed spikes match active supplier corridors **24–72h before SAP shows a delay**, staging localized (`PL`/`KO`/`ZH`/`DE`) + English check-ins behind anti-spam & human gates (`POST /api/risk/preemptive-chase`).
- **Q&A Weapon #2 — "How do you fix single-source risk after the weekend?" (2nd-Source Scout):** Click **Scout 2nd Sources →** on any single-source `CCC` part (`CMP-GAM-010`, `CMP-MAG-005`) — excludes embargoed origins (`CN`/`IN`), ranks safe corridors (`DE`/`PL`/`JP`) via live feeds, proves simulated `70/30` **`CCC → A`** recovery, and copies a spec-grounded RFQ in 1 click.
- **Q&A Weapon #3 — "What stops an autonomous AI from ordering from a failing vendor?" (Circuit Breakers):** Point to **Risk-Triggered Circuit Breakers** — config-driven Grade 5 (`B`) / Grade 6 (`CCC`) rules automatically lock `Control` mode, freeze unattended auto-POs, upgrade dock QC to `100% AQL`, and shift dual-source volume to the healthy alternate supplier (all logged to `/observability`).
