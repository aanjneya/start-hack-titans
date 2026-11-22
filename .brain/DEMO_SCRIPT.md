# Demo Script (2–3 Minutes)

> Write the exact judge demo flow early. Every feature we build should map directly to a beat below — cut anything that doesn't appear in the demo.

## 1. The Hook (0:00 – 0:30)
- **Problem**: _TBD_
- **Opening visual**: _TBD_

## 2. Live Demo Flow (0:30 – 2:15)
1. Start the offline sleeve demo from the repository root on `main` with `backend/.venv/bin/python -m backend.planner_demo`. It replays through the 17:23 drop in an isolated database. Open the Standard sleeve Finding and show the blocked PackRight lot alongside the two Karton rev C lots.
2. Compare the €1,215 fresh print (zero extra copies by Sunday) with confirming the existing delivery. Approve the information request and open the simulated Chaser outbox.
3. Replay the labeled site release and supplier reply. Show the accepted 20,000 sleeves, unchanged Standard total, and game-card constraint. The decision avoids a rush charge with no on-time benefit.

**If a judge asks what changed by evening**, show the 21:38 receipt for SHP-2026-04313. The old sleeve request is outdated. The forecast projects 34,800 Standard copies short by Sunday. The card recommends reviewing game-card availability (CMP-GAM-001). Approving it opens one internal buyer review.

**Buyer approval demo.** Start `backend/.venv/bin/python -m backend.planner_demo --serve-pickup` and open the control room as Paul Winter. The simulated €500 pickup quote raises the PO value to €53,150. Open **Why?** to show the €50,000 threshold and **Awaiting Category lead**, assigned to Sofia Marchetti. Paul can approve his own €0 supplier inquiry. The demo uses an isolated mock database.

## 3. Backup Plan
- **Layer 1 (Primary)**: Live deployed cloud URL from `main` (no single-laptop/HDMI failure point).
- **Layer 2 (Wi-Fi / API drop)**: Flip `MOCK_MODE=true` instantly via runtime toggle (`?mock=1` or UI hotkey with subtle `MOCK` indicator badge).
- **Layer 3 (Total fallback)**: Tag `demo-safe` on the last green `main` commit 60 min before submission + record a 60–90s backup screen recording.
