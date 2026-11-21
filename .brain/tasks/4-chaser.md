# 4 · Chaser

**Owner:** unclaimed

## What this agent does
When information is missing or unclear, it writes a short, polite email to the right supplier or carrier, **in their language**, naming the exact order or shipment. It reads the reply and feeds the answer back into the database. It never spams: one open question per supplier at a time.

## Why we need it
Today buyers chase suppliers by phone every day ("the team is exhausted"). It's also a "real action" the judges want to see. **Warning from kickoff:** the suppliers answer by AI email, and **if we spam them they stop answering**.

## Example from our real data
Standard sleeve CMP-GAM-004 must now be rev C. Karton Nowak (Poland) has a machine-downtime notice **and** an express offer dated today. The Chaser asks, in Polish: "Is the express run for order 45003… made in revision C? Please confirm by 18:00."

## What it works with
- **Gets:** "missing info" issues from the **Reconciler**; "please send this" actions from the **Launch Planner** after a human approved them.
- **Gives:** outgoing emails (outbox) → suppliers; replies → **Ingest** (as new facts) and the agent log.

## Tasks
- [ ] **T4.1** Write the email from an issue: the supplier's contact and language come from their profile; include order, shipment and part numbers.
- [ ] **T4.2** Anti-spam rule: max one open question per supplier, and a minimum gap between emails.
- [ ] **T4.3** Preview mode: a human can see and approve the first email to each supplier (in the demo it can auto-send).
- [ ] **T4.4** Demo supplier simulator: fake replies for offline demos (e.g. Karton Nowak: "yes, rev C, 15,000 pcs by Sun 08:00").
- [ ] **T4.5** Read real replies and turn them into facts for the Ingest agent.
- [ ] **T4.6** Escalate to a buyer if there's no reply within X hours.

## When it's done
- A "missing info" issue produces a correct Polish email in the outbox, gets a (simulated) reply, and the reply shows up in the database and the log.
- It refuses to send a second email to the same supplier too soon.

## Open question for the team
Which email address do we send from? Suppliers only answer **sender addresses registered in the portal Settings**. We need one shared mailbox + a way to send (SMTP or an email API).

---
### Technical notes (for Claude Code, refine later)
- Supplier contact: `suppliers/<ID>/profile.json → supplier.contact{name, email, language}` (CSV fallback: `contact_email`, `preferred_language`).
- Tables: `outbox(id, supplier_id, issue_id, lang, subject, body_original, body_en, status draft|approved|sent|replied, sent_at)`, `inbox(...)`.
- Draft with `claude-sonnet-5-5`; store an English copy for the trace drawer. Emit `reply.received{supplier_id, text, attachments}`.
