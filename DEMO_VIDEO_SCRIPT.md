# Demo video script (about 2:00, standalone)

Judges may watch this before the pitch, or instead of it. It assumes they know nothing.
Voice: ElevenLabs, cloned voice "Daksh Verma". Screen: `localhost:3100` at 1440×900.
Only features that exist on the `daksh` branch are shown. Early pay, ECB FX and the supplier magic link are not built, so they are not in the video.
Three things must land: **recommendations** (ranked fixes with € and copies), **human in the loop** (approval limits, one-click sign-off, audit log), and **we calculate the risk ratings ourselves** (from named factors, recalculated live when new data arrives).

## Voiceover and clicks

| # | Screen and clicks | Voiceover (word for word) |
|---|---|---|
| 1 | **Control room.** Countdown, then the four agent cards. | Soony launches Grand Heist 6 in thirty-one hours. Their SAP sees only two of the seven supply steps. The rest hides in supplier PDFs, emails and dock scans. Our six AI agents watch all of it in parallel, and turn it into recommendations that a person approves. |
| 2 | **SAP data** (goods receipts), close. Open the **Art Book** card: copies at risk → **Best actions** (ranked) → €16,800 → **More details** → **Who approves** → **Open review**. Close, click **Approvals on**: "Every action waits for its approver". | Ingest turns every file into clean SAP tables, with the source on every row. Here, the Collector's Edition is fifty-nine hundred art books short. The Planner recommends ranked fixes, each with its cost and the copies it wins back. The best one: twenty-five hundred copies from a Polish offer, for sixteen thousand eight hundred euros. And a human stays in the loop. Approval limits route each action to the right person, the buyer approves with one click, and no agent acts on its own. |
| 3 | **AI agents → Risk.** Header "AAA–CCC from our data and 5 outside feeds", **Ratings by grade**, **What drives supplier risk** (status and notices, open checks, weather and satellite, Polymarket, new rules). Click **Play next data drop** on this page: the ratings recalculate live and the **Guangzhou PackRight · CCC** card appears with its two reasons and sources. | Now, risk. Nobody hands us these ratings. Our Risk Rater calculates its own, from triple A to triple C, for every supplier, part and edition. It scores our own data, like supplier status, notices, open checks and stock, plus outside feeds: Polymarket odds, weather, satellite tracking, news and new regulations. Then new data arrives: an export licence notice from Guangzhou PackRight. We recalculate, and PackRight drops from A to triple C, with its top reasons and sources. |
| 4 | Same page: **Path to a better rating** (tick the four actions, CCC → BBB), **Circuit breakers** ("Human Gate Required", "Auto-POs Frozen"), **Early warnings**, **Scout 2nd Sources**. | Then it acts. It recommends the cheapest way back: these four actions lift the Collector's Edition from triple C to triple B, for seventeen thousand euros. Circuit breakers freeze automatic orders to PackRight until a person signs off. Outside signals warn the Chaser days before SAP sees a delay, and the scout finds safer second sources. |
| 5 | Sidebar **Human**: "Signed by Paul Winter at …". Then **Back to Control Room**, hold. | Every step is logged: which agent did what, from which file, and who signed off. Nothing is hard-coded to Soony. One setup file holds the approval limits and data sources. We keep the line running. |

About 300 words at roughly 165 words a minute.

## Before recording

1. Restart the demo backend so the Art Book offer is still open and the drop with the export licence notice has not played yet: launch config `backend-demo-soony` (port 8000).
2. Build and start the web app: `npm --prefix web run build`, then launch config `web-prod` (port 3100).
3. Open `http://localhost:3100` at 1440×900, 100 % zoom, no bookmarks bar, notifications off.
4. Do not click anything before recording. Approving and playing the drop are part of the take.
5. To record again, restart the backend first (step 1).
