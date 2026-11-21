# Lio challenge: end-to-end process and agents ("Launch Line Guard", working name)

## Context
The 16:00 team discussion was all about the **middle**: getting messy supplier data into one source of truth, finding gaps, and chasing suppliers. It never pinned down the **start** (what triggers the system and what it watches) or the **end** (what decision comes out, who acts on it, and how we know it worked). The judges want an end-to-end system where "agents take real actions (decide, write, escalate)", plus "a clear recommendation and one click to act". The CPO memo (16:08 drop) asks for "one page per day on what can stop a line. Not forty dashboards."

The goal: one loop everyone on the team can explain in 30 seconds, with 5 agents plus a coordinator, each with a clear job, inputs, outputs and human gate, built in priority order for the Sunday 12:00 submission.

## The process in one sentence
**Every drop or email → normalize into one truth → check it against what the launch needs → chase what's missing → recommend a fix with cost and copies saved → a human approves in one click → the agent executes and checks it worked.**

```
 SENSE            UNDERSTAND          CHECK               ACT                     DECIDE            CLOSE THE LOOP
 portal drops  →  1 Intake &      →   2 Reconciler    →   3 Chaser (emails)   →                 →  agents re-check after
 supplier mail     Normalizer          (gaps, conflicts,   4 Launch Planner        human approves     action; outcome logged;
 (15-min watch)    (one ID, one        stale, revisions)      (coverage, Plan B, →  in one click       risk score updated
                    format)                                  cost vs copies)        (policy PP-07)
                                        5 Risk Rater feeds 2 and 4 continuously
              Coordinator: routes handoffs + writes every step to the Trace log (AI traceability)
```

## Stages and agents

| Stage | Agent | Input | What it does (real actions) | Output / handoff | Human gate |
|---|---|---|---|---|---|
| 0 Sense | **Watcher** (exists: `monitor/drop_watch.py`) | Portal `/api/v1/files` SHA list, every 15 min | Detects added, changed and removed files and downloads only those | A "new evidence" event per file → agent 1 | none |
| 1 Understand | **Intake & Normalizer** | New files (CSV with `;`/`,` and legacy SAP columns like `EBELN`/`MENGE`, XLSX, PDF, DOCX, supplier docs in KR/CN/PL/EN) | Maps every file into canonical tables: Shipment, PO line, Receipt, Stock, Notice, BOM/Revision. Translates and maps supplier numbering to `CMP-…`/`4500…`. Reads columns by meaning, never by exact name (`products.csv` already renamed one today) | Truth-store rows with a `source` + `confidence` on every field. Anything it can't map goes to agent 2 as "unclear" | none |
| 2 Check | **Reconciler** | Truth store + `launch_status`/BOM/products | Runs rules plus LLM judgment: ERP vs tracker conflicts (e.g. SHP-04276 `arrived_port` vs `in transit`), stale `updated_at`, PO vs order confirmation qty/date, receipts vs stock, **revision checks** (CMP-GAM-004 rev B must not be packed), IBAN vs contract, quantity jumps (690 vs 790) | **Issues** (typed, severity, evidence links) → agent 3 if information is missing, → agent 4 if it threatens copies | flags quantity jumps and IBAN changes for a human; never auto-accepts them |
| 3 Act: ask | **Chaser** | Issues of type "missing / unclear" | Writes and sends emails to suppliers and carriers (from the registered sender in Settings), in their language, citing the PO/shipment. Parses the reply and feeds it back into agent 1 | Reply → agent 1. No reply within X h → escalate to the buyer | first email per supplier gets human preview (demo: auto) |
| 3 Act: plan | **Launch Planner** | Issues + coverage per part + `launch_status` rates (Std 6,000/h, CE 900/h) + packing hours left | Computes "can we pack N copies by Sun 20:00?" per edition and part. **Collector's first** (CPO memo). Generates options: air freight, courier, split shipment, reallocate parts from Standard to CE, use up old revision where allowed. Prices each as **€ cost vs copies saved** | **Decision card**: problem, 2–3 options, recommendation, cost, copies saved, deadline to decide | anything with budget above policy PP-07 → approver named in `approval_policy_PP-07.pdf` / org chart |
| 4 Decide | **Human (Maren / Jonas)** | Decision card on the one-page board | One click: approve / reject / ask | Approval → agent executes (rebook email, PO change request, chase) | this *is* the gate |
| 5 Close loop | Planner + Reconciler | Next drops and replies | Confirms the action happened (new ETA, confirmation received, receipt booked). Marks the issue resolved or reopens it | Outcome in the trace log; feeds the risk score | none |
| always | **Risk Rater** ("Moody's for parts") | Truth store, history of issues, news drops | Scores each part and supplier 1–6 from explainable factors: single source, on-time history from `goods_receipts` vs PO dates, open issues, country/news events, lead time vs launch. No magic numbers: each score shows its top 3 reasons | Score + reasons → agents 2 and 4, plus the board | none |
| always | **Coordinator** (code, not an LLM) | All agent events | Routes handoffs, holds state, writes every read/decision/action with its evidence to the **Trace log** | Trace view = AI traceability dashboard | none |

**Why this split:** each agent has one verb (normalize, check, ask, plan, score). That answers the judges' question "tool use, reasoning, state, not one big prompt?" and settles the 1-vs-2-vs-3 debate from the discussion.

## What the user sees (one product, two views)
1. **"What can stop a line" page** (for Maren and Jonas, matching the CPO memo): hours to launch; per edition, packed / to pack / projected at 20:00; a red/yellow/green list of blocking parts; decision cards with one-click approve. Everything else hidden behind "details".
2. **Agent trace** (for Nils/IT and the judges): a timeline of what each agent read, decided and did, with links to the source files. This is your AI-traceability must-have, in the same app.
3. **Risk table**: parts and suppliers with their score and top reasons.

## Worked example to build and demo first (real data, today)
1. 16:08 drop: `bom_delta` says CMP-GAM-004 rev B must not be packed → **Reconciler** flags the rev B stock and rev B in-transit parts as unusable.
2. **Planner**: Standard needs X of part 004 at rev C; coverage falls short by Y copies; options: Karton Nowak road shipment 04313 (check the revision on the order confirmation), a rush rev C order, or an air option. Costs vs copies.
3. **Chaser** emails Karton Nowak (Polish): "Is SHP-04313 rev C? Confirm by 18:00."
4. Card on the board → Maren approves the rush order → agent sends it → the next drop or reply confirms it → issue closed → the trace shows the whole chain.
Backup example: CMP-GAM-010 newly `critical=Y` while both open shipments arrive in December.

## Build order (Sat 17:00 → Sun 12:00, 4 people)
| Priority | Slice | Suggested owner |
|---|---|---|
| P0 | Truth store + **Intake & Normalizer** for the ERP CSVs, both PO formats, the tracker xlsx, `bom`/`bom_delta`, `launch_status` (reuse `office_text`/`csv_summary` from `monitor/drop_watch.py`) | 1 person |
| P0 | **Reconciler** rules: ERP vs tracker, stale `updated_at`, revision check, PO vs confirmation | 1 person |
| P0 | **Planner** coverage math + decision card with cost vs copies; one-click approve | 1 person |
| P0 | One-page board + agent trace view (one design language) | 1 person |
| P1 | **Chaser** sending real email via the registered sender + parsing one reply | whoever finishes first |
| P1 | **Risk Rater** with 4 explainable factors | — |
| P2 | Supplier scout (second source, per the memo), news ingestion | after the hackathon / pitch slide only |
| always | `MOCK_MODE=true` fixtures from `challenge/start-hack-data/data/` so the demo survives the Wi-Fi | team |

## Pitch storyline (3 min)
Problem in 30 s ("We find out at the dock", plus a quote from Nils/Felix) → live: drop arrives → agents flag the revision trap → chase + plan → Maren approves in one click → trace shows why → business: € per missing Collector's copy × copies saved; any brand with a hard launch date (games, consumer electronics, fashion drops) has the same problem.

## Verification
- Replay the real drops: baseline (14:15) then the 16:08 drop from `challenge/start-hack-data/versions/` and `data/`. The system must raise the CMP-GAM-004 revision issue and the CMP-GAM-010 critical-part issue **without code changes** ("when a new drop arrives, does your system cope?").
- Schema-change test: feed the old and new `products.csv`. The normalizer must still map `weekly_plan_qty`.
- End-to-end click test: issue → decision card → approve → outbound email logged → trace entry with evidence links.
- Run with `MOCK_MODE=true` and with the network off.

## Not doing now
No code is written or pushed in this step. After you approve, I can save this as `.brain/ARCHITECTURE.md` in the team repo (local only unless you say push), or split the P0 slices into tasks for the four of you.
