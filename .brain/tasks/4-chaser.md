# 4 · Chaser

**Owner:** @tim

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
- [x] **T4.0** (@tim) Work from `missing_info` Findings (`.brain/DATA_FORMAT.md`): use their ids, summary and `suggested_actions` (type `email`); set the finding's status to `asked` when the email is out.
- [x] **T4.1** (@tim) Write the email from an issue: the supplier's contact and language come from their profile; include order, shipment and part numbers.
- [x] **T4.2** (@tim) Anti-spam rule: max one open question per supplier, and a minimum gap between emails.
- [-] **T4.3** (@tim) Dropped (team decision): no separate approval route. Planner mails already follow a human decision approve; issue mails are guarded by the anti-spam rule + `CHASER_SEND` switch (drafts only when off).
- [x] **T4.4** (@tim) Fallback only (`MOCK_MODE=true`): replay a recorded **real** mail exchange (fixture in repo), no invented supplier replies.
- [x] **T4.5** (@tim) When a reply arrives (real mailbox or mock simulator), store it with `system.store_reply(...)` and emit `reply.received`. Don't write data yourself: Ingest applies it, then the Reconciler re-checks.
- [x] **T4.6** (@tim) Escalate to a buyer if there's no reply within X hours.

## When it's done
- A "missing info" issue produces a correct Polish email in the outbox, gets a (simulated) reply, and the reply shows up in the database and the log.
- It refuses to send a second email to the same supplier too soon.

## Mailbox (resolved Sat ~20:00, @tim)
- Shared team mailbox on **GMX**, registered in the portal under Settings -> Mail Senders. SMTP + IMAP login tested OK.
- Credentials only in `.env` (`MAIL_ADDRESS`, `MAIL_PASSWORD`, SMTP/IMAP hosts, see `.env.example`); ask tim for the values, never commit them.
- **Only ONE instance sends and polls the mailbox** (tim's during dev, the deployed server for the demo), otherwise replies get split across DBs.
- Design: Chaser = real tool-use agent (Claude decides what to ask, when to follow up/escalate); rate limit enforced in code inside the tools.

---
### Technical notes (for Claude Code, refine later)
- Supplier contact: `suppliers/<ID>/profile.json → supplier.contact{name, email, language}` (CSV fallback: `contact_email`, `preferred_language`).
- Tables: `outbox(id, supplier_id, issue_id, lang, subject, body_original, body_en, status draft|approved|sent|replied, sent_at)`, `inbox(...)`.
- Draft with `claude-sonnet-5-5`; store an English copy for the trace drawer. Emit `reply.received{supplier_id, text, attachments}`.

## Status (Sat 22:05, on `main` since 21:45, `5480c9e`)
- Live: real tool-use agent + GMX mailbox; Planner contract done (one mail per `decision_id`, approved decisions skip the open-question limit, MOCK marks mails `sent`).
- Tests: `test_chaser.py` 10 green; with Jivi's planner fix `d6e40fe` planner + contract + chaser = 19 green.
- Open: first REAL mail to Karton Nowak (`CHASER_SEND=true` on ONE instance), then `python -m backend.agents.chaser export <outbox_id>` to store the real exchange as demo fallback.
