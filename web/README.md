# web: line control screen (basic)

One page that shows what the agents do: the forecast per edition (copies missing at close), the decision
cards to approve, the six agents working, a live log, and tabs for findings, supplier emails and risk.
It only talks to the FastAPI backend (`API_CONTRACT.ts` types, polled every 3 s).

## Run it (two terminals, from the repo root)

```bash
MOCK_MODE=true CHASER_SEND=false REPLAY_DB_PATH=backend/data/ui-demo/replay.db backend/.venv/bin/uvicorn backend.app.main:app --port 8000
```

```bash
npm --prefix web install && npm --prefix web run dev
```

Open http://localhost:3000 and press **Play next data drop**: each click sends the next real portal drop
(from git history) through all agents. The demo resumes the last completed drop in the dedicated
`REPLAY_DB_PATH`. Choose a fresh file for a new rehearsal.

- Other backend address: `NEXT_PUBLIC_API_URL=http://host:8000`.
- In mock mode nothing leaves the laptop: emails show as **Simulated**, the AI answers are canned.
- "Send test reply" under an email feeds a supplier answer into the loop (inbox → Ingest → Reconciler/Planner).
- `./scripts/verify.sh` lints and builds this folder, so run `npm --prefix web install` once after pulling.

## Buyer approval demo

Run `backend/.venv/bin/python -m backend.planner_demo --serve-pickup` from the repository root, then start the frontend with `npm --prefix web run dev`. This mock backend uses its own temporary database. It replays the 17:23 snapshot, simulates a site recount and dock release, and records a €500 pickup quote. In the control room, Paul Winter can see the quote's extra charge, amended PO value and category lead. Open **Why?** for the approval band. Paul can approve his own proposals. The demo seat simulates identity without employee login.


## Rehearse the risk decision

Use one fresh replay database throughout preparation and presentation. From the repository root run:

```bash
export MOCK_MODE=true CHASER_SEND=false
export REPLAY_DB_PATH="backend/data/rehearsals/buyer-$(date +%s).db"
backend/.venv/bin/python -m backend.core.replay --upto 2
backend/.venv/bin/uvicorn backend.app.main:app --port 8000
```

In a second terminal run `npm --prefix web run dev`. Open the buyer screen and play the next historical drop. In **Future supply**, expand **Why?** to see the change in supplier grade, external sources, dated order lines, and the supplier’s sample offer. **Start qualification review** opens the assigned internal record in Decision follow-up.

For the separate financial scenario use `backend/.venv/bin/python -m backend.planner_demo --serve-inquiry` as the backend. **More packing options** contains the Standard supplier request. Open its thread in Decision follow-up. For the fixture, enter:

```text
Pickup quote. PO 4500318775/2, SHP-2026-04313, 20,000 units, rev C, arrival 2026-11-21T15:00+01:00, incremental cost EUR 500, valid until 2026-11-21T15:00+01:00.
```

**Review priced offer** shows the fee, the amended PO, and Sofia’s approval route. The fixed buyer can inspect it. The Planner integration test simulates Sofia signing and feeds a threaded mailbox confirmation through Chaser and Ingest.


On a future-supply card, expand the Polymarket line for the market question and its source mode. **Compare sample timing** compares the supplier offer with the earliest matching PO delivery. Under **Why?**, open the supplier's original passage and the supplier, component and product grades. **Decision history** follows the finding, signer, review or supplier conversation. In the pickup simulation it includes the original inquiry and the forecast supported by the matched reply. **How the amended PO adds up** shows the lines behind the signing limit.

After opening a qualification review, expand **Open assigned review** in **Decision follow-up**. The separate **Send sample inquiry** action authorizes one Chaser message asking for specifications and terms. Its supplier thread accepts a clearly labeled test reply on the demo backend. Ingest links the reply to the review for evaluation.
