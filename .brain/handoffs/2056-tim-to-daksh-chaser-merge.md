# Handoff 20:56 · tim → daksh · Chaser ready on branch `tim` (needs your OK to merge)

**Branch:** `tim` (not pushed to main, per your request). Commits: `836b281` chaser, `eb48d41` + `a9cc96a` time hint / llm key fix, plus this brain update.

## What's ready
- `backend/agents/chaser.py`: real tool-use agent (Claude + 6 tools: get_finding, get_supplier_contact, get_mail_history, read_evidence_file, send_email, escalate_to_buyer). Consumes `missing_info` findings + `email.requested`; sets finding to `asked`; replies via `system.store_reply` + `reply.received` (Ingest stays the only writer).
- `backend/core/mail.py`: GMX SMTP (STARTTLS) + IMAP; replies matched by `In-Reply-To`.
- Route `POST /api/chaser/poll`; optional timer `CHASER_POLL_MIN`.
- Tests: `backend/tests/test_chaser.py` (8 green). Live dry run with Claude OK (draft only).

## Shared hubs I touched (please review before merge)
| File | Change |
|---|---|
| `backend/core/config.py` | new settings: `MAIL_*`, `SMTP_*`, `IMAP_*`, `CHASER_SEND/MIN_GAP_MIN/ESCALATE_H/POLL_MIN/MAX_STEPS` (all with defaults) |
| `backend/core/models.py` | `OutboxMessage.message_id`, `.run_id`, status `escalated` (additive; `_add_missing_columns` handles old DBs) |
| `backend/app/main.py` | `_chaser()` helper, poll route, poll timer in `lifespan` |
| `API_CONTRACT.ts` | `OutboxMessage` +`message_id`, `run_id`, `'escalated'` |
| `backend/requirements.txt` | `tzdata` (Windows has no tz db) |
| `.env.example` | GMX mailbox + `CHASER_*` placeholders |

## New env vars
`MAIL_ADDRESS`, `MAIL_PASSWORD` (ask tim, never commit), `SMTP_HOST/PORT/SECURITY`, `IMAP_HOST/PORT/SECURITY`, `CHASER_SEND` (**true on ONE instance only**), `CHASER_MIN_GAP_MIN`, `CHASER_ESCALATE_H`, `CHASER_POLL_MIN`.

## For you
- 4 tests in `test_ingest_watcher.py` fail on Windows (path separators), not caused by the chaser.
- Tell tim when `tim` may go to main; tim runs the merge + `verify.sh`.

## Update 21:40
- Merged latest main into `tim` (34d37d8): took your `llm.py` (your explicit-key fix supersedes mine); Chaser now uses `llm._anthropic_client()` and falls back when `llm.provider() != "anthropic"`.
- Jivi's planner contract done: one mail per `decision_id`, approved decisions bypass the open-question limit. `test_planner.py` counts decision mails only (tell Jivi).
- Tests on Windows: 54 passed, 6 failed, all path-separator issues in `test_ingest_watcher.py` (5) + `test_reconciler.py` replay (1, also red on clean main).
- `tim` is 0 behind / ahead of main: the merge is a fast-forward.
