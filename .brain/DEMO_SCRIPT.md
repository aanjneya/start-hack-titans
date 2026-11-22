# Demo Script (2–3 Minutes)

> Write the exact judge demo flow early. Every feature we build should map directly to a beat below — cut anything that doesn't appear in the demo.

## Opening (0:00–0:30)

“Procurement told us they learned about single-source exposure too late. A new export rule just hit their supplier. Which orders are exposed, and what can the buyer do today?”

## Live demo (0:30–2:15)

1. Run the buyer screen against a dedicated replay backend on `jivi`. Play through the 17:23 export-control drop. Under **Future supply**, open the backup-source review for the Collector’s box. Source evidence connects PackRight’s notice, its own article number, Soony’s BOM and affected open PO lines. The box lead time rose from 63 to 105 days for later deliveries. Magnets already installed in finished boxes still reach the weekend line.
2. Click **Start qualification review**. Show the named owner and the internal review. This approval authorizes zero spend. Qualification cost and future order volume remain unknown. Open the risk report for the supplier’s grade and external evidence if asked. Identify offline market examples as simulations.
3. In the separate historical pickup scenario, show Paul the €500 quote, the €53,150 amended PO and the required signer, Sofia Marchetti. The regression with the isolated mailbox proves that Sofia’s approval sends one request and a matched confirmation increases Standard by 5,200. Label the recount, site release and reply as simulated. Start the quote screen with `backend/.venv/bin/python -m backend.planner_demo --serve-pickup`.

**Evening replay.** At the 21:38 drop Hamburg projects 608,000 Standard (32,000 short, clear cases) and 66,100 Collector’s (5,900 short, art books). Treat the terminal pickup and book offer as conditional recoveries.

## 3. Backup Plan
- **Layer 1 (Primary)**: Use the verified `jivi` buyer screen with the dedicated demo database.
- **Layer 2 (Wi-Fi / API drop)**: Open the prepared local replay backend with `MOCK_MODE=true`.
- **Layer 3 (Total fallback)**: Record a 60–90s backup screen recording.
