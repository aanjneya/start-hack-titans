# web: line control screen (basic)

One page that shows what the agents do: the forecast per edition (copies missing at close), the decision
cards to approve, the six agents working, a live log, and tabs for findings, supplier emails and risk.
It only talks to the FastAPI backend (`API_CONTRACT.ts` types, polled every 3 s).

## Run it (two terminals, from the repo root)

```bash
MOCK_MODE=true CHASER_SEND=false DB_PATH=backend/data/ui-demo/truth.db backend/.venv/bin/uvicorn backend.app.main:app --port 8000
```

```bash
npm --prefix web install && npm --prefix web run dev
```

Open http://localhost:3000 and press **Play next data drop**: each click sends the next real portal drop
(from git history) through all agents. The first click starts from an empty store, so use a separate
`DB_PATH` as above and your normal `backend/data/truth.db` stays untouched.

- Other backend address: `NEXT_PUBLIC_API_URL=http://host:8000`.
- In mock mode nothing leaves the laptop: emails show as **Simulated**, the AI answers are canned.
- "Send test reply" under an email feeds a supplier answer into the loop (inbox → Ingest → Reconciler/Planner).
- `./scripts/verify.sh` lints and builds this folder, so run `npm --prefix web install` once after pulling.

## Buyer approval demo

Run `backend/.venv/bin/python -m backend.planner_demo --serve-pickup` from the repository root, then start the frontend with `npm --prefix web run dev`. This mock backend uses its own temporary database. It replays the 17:23 snapshot, simulates a site recount and dock release, and records a €500 pickup quote. In the control room, Paul Winter can see the quote's extra charge, amended PO value and category lead. Open **Why?** for the approval band. Paul can approve his own proposals. The demo seat simulates identity without employee login.
