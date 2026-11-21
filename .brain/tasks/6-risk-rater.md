# 6 · Risk Rater ("Moody's for parts")

**Owner:** @aanjneya

## What this agent does
It gives every part and every supplier a risk grade from **1 (safe) to 6 (critical)**, also shown as AAA–CCC, and always explains the **top 3 reasons**. It updates whenever new data or news arrives. It doesn't fix anything itself; it tells the Reconciler and the Planner where to look first.

## Why we need it
Our "Moody's" idea from the 16:00 discussion, and a differentiator for the judges. We agreed we **can't bluff the score**: every grade must be explainable from data, not invented. The CPO also wants a second source for every single-source part after the launch.

## Example from our real data
**CMP-MAG-005** lid magnets (17:23 drop): one supplier, which depends on a Chinese sub-supplier that now can't export (news: China tightens magnet export licences, 4–8 weeks). Stock 0, no order on file, 4 magnets per Collector's box → grade **6 (CCC)**. Reasons: single source, export ban, no stock.

## What it works with
- **Gets:** parts, suppliers, orders, shipments, stock and news from the database; open issues from the **Reconciler**.
- **Gives:** a grade + reasons per part and supplier → **Reconciler** (what to check first), **Launch Planner** (which fixes are safe), and later the risk table on screen.

## Tasks
- [ ] **T6.1** Pick 4–5 simple, explainable factors: how many suppliers a part has, hours of buffer before the line stops, supplier health (approval status, downtime notices), transport risk (sea vs air, stale tracking), news hitting the supplier's country.
- [ ] **T6.2** Calculate a 1–6 grade per part and per supplier from those factors (simple, transparent weights).
- [ ] **T6.3** Write the top 3 reasons in plain words, each linked to its source file.
- [ ] **T6.4** Recalculate when new data or news arrives, and log grade changes (e.g. "PackRight: 3 → 5 after export notice").
- [ ] **T6.5** List single-source parts as candidates for a second source (CPO memo, post-launch).

## When it's done
- After the 17:23 drop, CMP-MAG-005 and Guangzhou PackRight jump to high risk with the right reasons.
- Anyone can read why a grade is what it is.

---
### Technical notes (for Claude Code, refine later)
- Factors from: `suppliers/*/components.csv` (`share`, `primary`), `components_delta.csv`, `profile.json` (`status`, `qualification`), `soony/news/*.json` (`regions`, `tags`), `stock`, `shipments` (`mode`, `updated_at`), Planner's buffer hours.
- Deterministic scoring in code (easy to defend to the judges); Claude only for writing the reason text. Table `risk_scores(entity_type, entity_id, grade, reasons[], computed_at)`; emit `risk.updated`.
