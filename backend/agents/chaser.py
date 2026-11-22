"""Chaser agent (owner: tim, .brain/tasks/4-chaser.md).

A real tool-use agent: Claude gets one `missing_info` finding (or a planner request) and DECIDES with tools
whether to email, what exactly to ask, or whether to escalate. Safety rules live in code, inside the tools,
so the model cannot bypass them:
  - one open question per supplier + minimum gap between mails (CHASER_MIN_GAP_MIN)
  - mail only to the supplier of the finding, address from the data (never from the model)
  - real sending only when CHASER_SEND=true (exactly ONE instance: tim's in dev, the server in the demo)
Replies: poll_inbox() reads the shared mailbox, stores them via system.store_reply and emits reply.received;
Ingest applies them, the Reconciler re-checks. No reply after CHASER_ESCALATE_H -> human_check finding.

Fallback (MOCK_MODE=true, no network): deterministic template mail as a draft, and if a recorded REAL exchange
exists in backend/fixtures/chaser/<SUP-ID>*.json it is replayed as the reply. No invented supplier answers.
"""
from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path
from typing import Any, Optional

from sqlmodel import select

from ..core import events, llm, trace
from ..core import mail as mailbox
from ..core.config import BACKEND_ROOT, get_settings
from ..core.coordinator import Event
from ..core.models import InboxMessage, Issue, IssueKind, OutboxMessage, Supplier, utcnow
from ..core.parsing import file_text, safe_rel
from ..core.store import session
from .base import Agent, register

FIXTURE_DIR = BACKEND_ROOT / "fixtures" / "chaser"
OPEN_STATUSES = ("draft", "approved", "sent")

SYSTEM_PROMPT = """You are the Chaser, a procurement agent for a company preparing a hard launch deadline.
You get ONE finding where information from a supplier or carrier is missing or unclear. Your job: get exactly
that information with as little friction as possible.

How to work:
1. Call get_finding first. Then check get_mail_history: never ask again what was already asked or answered.
2. If the evidence files might already contain the answer, read them (read_evidence_file) before writing.
3. If an email is needed, call send_email ONCE: short (max 6 sentences), polite, in the supplier's language
   (from get_supplier_contact), naming our exact order / shipment / part numbers, ONE clear question and a
   reply-by time. Also give an English translation in body_en. No pressure, no threats, no guessing numbers.
4. Escalate to a buyer (escalate_to_buyer) instead of emailing when: the supplier has no contact address,
   the matter needs a commercial decision (prices, bank details, contract changes), or send_email refuses
   because a question is still open and the finding is severity 5.
5. If get_finding shows an approved_decision_id, a human already approved this action: send exactly the
   requested message (order / confirmation request with our references) and do not escalate it again.
6. Finish with one sentence saying what you did and why.

Suppliers stop answering if they are spammed: when send_email refuses, accept it and do not retry."""


def _fixture_for(supplier_id: str) -> Optional[dict]:
    if not supplier_id or not FIXTURE_DIR.exists():
        return None
    for path in sorted(FIXTURE_DIR.glob(f"{supplier_id}*.json")):
        try:
            return json.loads(path.read_text(encoding="utf-8")) | {"_path": path.name}
        except (OSError, json.JSONDecodeError):
            continue
    return None


@register
class Chaser(Agent):
    name = "chaser"
    subscriptions = {events.ISSUE_RAISED: "on_issue", events.EMAIL_REQUESTED: "on_email_requested"}

    # -- events --------------------------------------------------------------
    def on_issue(self, event: Event) -> None:
        if event.payload.get("kind") != IssueKind.MISSING_INFO:
            return
        issue_id = event.payload.get("issue_id")
        with session() as s:
            issue = s.get(Issue, issue_id) if issue_id else None
        if issue is None:
            return
        if issue.status in ("asked", "resolved"):
            self.log("skip", f"finding #{issue.id} already {issue.status}", data={"issue_id": issue.id})
            return
        self.chase(issue_id=issue.id, supplier_id=issue.supplier_id)

    def on_email_requested(self, event: Event) -> None:
        """Planner hand-off after a human approved a decision (Jivi's contract, handoff 2055):
        payload {supplier_id, decision_id, issue_id, po_number, po_line, shipment_id, part, ask}.
        Exactly one mail per decision_id; the decision_id is kept on the outbox row."""
        p = event.payload
        decision_id = p.get("decision_id")
        if decision_id:
            with session() as s:
                done = s.exec(select(OutboxMessage).where(OutboxMessage.decision_id == decision_id)).first()
            if done:
                self.log("skip", f"decision #{decision_id} already has mail #{done.id}",
                         data={"decision_id": decision_id, "outbox_id": done.id})
                return
        request = {k: p.get(k) for k in ("po_number", "po_line", "shipment_id", "part") if p.get(k)}
        self.chase(issue_id=p.get("issue_id"), supplier_id=p.get("supplier_id", ""),
                   ask=p.get("ask", ""), decision_id=decision_id, request=request)

    # -- main entry ----------------------------------------------------------
    def chase(self, *, issue_id: Optional[int], supplier_id: str = "", ask: str = "",
              decision_id: Optional[int] = None, request: Optional[dict] = None) -> dict:
        issue = None
        if issue_id:
            with session() as s:
                issue = s.get(Issue, issue_id)
        supplier_id = supplier_id or (issue.supplier_id if issue else "")
        if not supplier_id:
            self.log("skip", f"finding #{issue_id}: no supplier_id, nobody to ask", data={"issue_id": issue_id})
            return {"status": "skipped", "reason": "no supplier"}
        ctx = {"issue": issue, "issue_id": issue_id, "supplier_id": supplier_id, "ask": ask,
               "decision_id": decision_id, "request": request or {}, "outbox_id": None, "escalated": False}
        if llm.is_mock() or llm.provider() != "anthropic":
            # the tool-use loop needs Claude; with no Anthropic key (or MOCK_MODE) use the deterministic fallback
            return self._fallback(ctx)
        try:
            return self._run_agent(ctx)
        except llm.LLMError as exc:
            self.log("error", f"agent failed, using fallback: {exc}", data={"issue_id": issue_id})
            return self._fallback(ctx)

    # -- data helpers (also used by the tools) -------------------------------
    def contact(self, supplier_id: str) -> dict:
        """Contact from the source of truth (Ingest's suppliers table), else from the supplier profile file."""
        with session() as s:
            rows = s.exec(select(Supplier).where(Supplier.supplier_id == supplier_id)
                          .order_by(Supplier.id.desc())).all()
        for row in rows:
            if row.contact_email:
                return {"supplier_id": supplier_id, "name": row.name, "contact_name": row.contact_name,
                        "email": row.contact_email, "language": row.language or "en", "source": row.source}
        return self._contact_from_files(supplier_id)

    def _contact_from_files(self, supplier_id: str) -> dict:
        root = get_settings().data_root / "suppliers"
        for folder in sorted(root.glob(f"{supplier_id}_*")) if root.exists() else []:
            pj = folder / "profile.json"
            if pj.exists():
                try:
                    sup = json.loads(pj.read_text(encoding="utf-8")).get("supplier", {})
                    c = sup.get("contact", {}) or {}
                    if c.get("email"):
                        return {"supplier_id": supplier_id, "name": sup.get("name", ""),
                                "contact_name": c.get("name", ""), "email": c["email"],
                                "language": c.get("language", "en"), "source": str(pj.relative_to(root.parent))}
                except (OSError, json.JSONDecodeError):
                    pass
            pc = folder / "profile.csv"
            if pc.exists():
                from ..core.parsing import read_csv

                _, rows, _ = read_csv(pc.read_bytes())
                if rows and rows[0].get("contact_email"):
                    r = rows[0]
                    return {"supplier_id": supplier_id, "name": r.get("name", ""),
                            "contact_name": r.get("contact_name", ""), "email": r["contact_email"],
                            "language": r.get("preferred_language") or "en",
                            "source": str(pc.relative_to(root.parent))}
        return {"supplier_id": supplier_id, "email": "", "error": "no contact found"}

    def blocked_reason(self, supplier_id: str, decision_id: Optional[int] = None) -> Optional[str]:
        """Anti-spam rule (T4.2): one open question per supplier and a minimum gap. None = allowed.

        Mails that execute a human-approved decision are never blocked by open questions (they are
        deduplicated per decision_id instead): a buyer's approved order must not wait for an unrelated reply."""
        if decision_id:
            with session() as s:
                dup = s.exec(select(OutboxMessage).where(OutboxMessage.decision_id == decision_id)).first()
            return f"decision #{decision_id} already has mail #{dup.id}" if dup else None
        gap = timedelta(minutes=get_settings().chaser_min_gap_min)
        with session() as s:
            rows = s.exec(select(OutboxMessage).where(OutboxMessage.supplier_id == supplier_id)
                          .where(OutboxMessage.decision_id.is_(None))
                          .order_by(OutboxMessage.id.desc())).all()
        open_rows = [r for r in rows if r.status in OPEN_STATUSES]
        if open_rows:
            r = open_rows[0]
            return f"question #{r.id} to {supplier_id} is still open (status {r.status}); wait for the reply"
        if rows:
            last = rows[0].sent_at or rows[0].created_at
            if last and _aware(last) + gap > utcnow():
                return f"last mail to {supplier_id} was less than {gap} ago"
        return None

    def history(self, supplier_id: str, limit: int = 10) -> list[dict]:
        with session() as s:
            out = s.exec(select(OutboxMessage).where(OutboxMessage.supplier_id == supplier_id)
                         .order_by(OutboxMessage.id.desc()).limit(limit)).all()
            inn = s.exec(select(InboxMessage).where(InboxMessage.supplier_id == supplier_id)
                         .order_by(InboxMessage.id.desc()).limit(limit)).all()
        items = [{"dir": "out", "id": r.id, "status": r.status, "subject": r.subject, "body_en": r.body_en or r.body,
                  "at": str(r.sent_at or r.created_at)} for r in out]
        items += [{"dir": "in", "id": r.id, "subject": r.subject, "body": r.body[:1500], "at": str(r.received_at)}
                  for r in inn]
        return sorted(items, key=lambda i: i["at"])

    def write_mail(self, ctx: dict, subject: str, body: str, body_en: str) -> dict:
        """Store + (maybe) send one mail. All safety rules are enforced here."""
        supplier_id = ctx["supplier_id"]
        reason = self.blocked_reason(supplier_id, ctx["decision_id"])
        if reason:
            self.log("skip_rate_limit", reason, data={"issue_id": ctx["issue_id"], "supplier_id": supplier_id})
            return {"sent": False, "refused": reason}
        contact = self.contact(supplier_id)
        if not contact.get("email"):
            return {"sent": False, "refused": f"no contact address for {supplier_id}"}
        settings = get_settings()
        live = settings.chaser_send and not llm.is_mock() and mailbox.is_configured()
        msg = OutboxMessage(supplier_id=supplier_id, issue_id=ctx["issue_id"], decision_id=ctx["decision_id"],
                            to_email=contact["email"], language=contact.get("language", ""), subject=subject,
                            body=body, body_en=body_en, status="draft", message_id=mailbox.new_message_id(),
                            run_id=trace.current_run_id())
        with session() as s:
            s.add(msg)
            s.commit()
            s.refresh(msg)
        ctx["outbox_id"] = msg.id
        evidence = [{"path": contact.get("source", ""), "locator": "contact", "quote": contact["email"]}]
        if live:
            try:
                mailbox.send(msg.to_email, subject, body, message_id=msg.message_id)
                self._set_outbox(msg.id, status="sent", sent_at=utcnow(), sent_copy_pending=True)
                self._file_sent(msg)
                self.log("send_email", f"#{msg.id} to {supplier_id} ({msg.language}): {subject}", evidence,
                         data={"outbox_id": msg.id, "issue_id": ctx["issue_id"]})
            except mailbox.MailError as exc:
                self._set_outbox(msg.id, status="failed")
                self.log("error", f"send #{msg.id} failed: {exc}", data={"outbox_id": msg.id})
                return {"sent": False, "outbox_id": msg.id, "error": str(exc)}
        elif llm.is_mock():
            # offline demo: delivery is simulated, so the Planner sees the decision as dispatched
            self._set_outbox(msg.id, status="sent", sent_at=utcnow())
            self.log("mock_send", f"#{msg.id} to {supplier_id} ({msg.language}) simulated (MOCK_MODE): {subject}",
                     evidence, data={"outbox_id": msg.id, "issue_id": ctx["issue_id"], "mocked": True})
        else:
            self.log("draft_email", f"#{msg.id} to {supplier_id} ({msg.language}) NOT sent (CHASER_SEND off): "
                     f"{subject}", evidence, data={"outbox_id": msg.id, "issue_id": ctx["issue_id"]})
        if ctx["issue_id"]:
            self._set_issue_status(ctx["issue_id"], "asked")
        return {"sent": live, "outbox_id": msg.id, "to": msg.to_email,
                "note": "sent" if live else "stored as draft only (sending disabled on this instance)"}

    def escalate(self, ctx: dict, reason: str) -> dict:
        issue = ctx["issue"]
        sid = ctx["supplier_id"]
        key = f"{sid}/{ctx['issue_id'] or ctx['decision_id'] or 'request'}"
        summary = f"Buyer needed: {reason}"[:160]
        f = self.raise_issue(
            type="chaser_escalation", kind=IssueKind.HUMAN_CHECK, severity=max(3, issue.severity if issue else 3),
            summary=summary, key=key, reason=reason, supplier_id=sid,
            part_id=issue.part_id if issue else "", po_number=issue.po_number if issue else "",
            shipment_id=issue.shipment_id if issue else "",
            suggested_actions=[{"type": "human_review", "label": f"Call or email {sid} personally", "target": sid,
                                "owner": "human"}],
            evidence=issue.evidence if issue and issue.evidence else [{"path": "", "locator": "chaser"}])
        ctx["escalated"] = True
        return {"escalated": True, "finding_id": f.id}

    # -- the agent ------------------------------------------------------------
    def _run_agent(self, ctx: dict) -> dict:
        from anthropic import APIConnectionError, APIStatusError, beta_tool

        settings = get_settings()
        agent = self

        @beta_tool
        def get_finding() -> str:
            """Get the finding (problem) you must clarify, incl. ids, impact, suggested actions and evidence files."""
            issue = ctx["issue"]
            data: dict[str, Any] = {"supplier_id": ctx["supplier_id"], "request_from_planner": ctx["ask"] or None,
                                    "approved_decision_id": ctx["decision_id"], **ctx["request"]}
            if issue:
                data.update({k: getattr(issue, k) for k in (
                    "id", "type", "severity", "summary", "reason", "part_id", "supplier_id", "shipment_id",
                    "po_number", "product_id", "impact", "suggested_actions", "evidence", "status")})
            return json.dumps(data, default=str, ensure_ascii=False)

        @beta_tool
        def get_supplier_contact() -> str:
            """Get the supplier's contact person, email address and preferred language."""
            c = agent.contact(ctx["supplier_id"])
            return json.dumps({k: v for k, v in c.items() if k != "source"}, ensure_ascii=False)

        @beta_tool
        def get_mail_history() -> str:
            """Get earlier mails to and replies from this supplier (oldest first)."""
            return json.dumps(agent.history(ctx["supplier_id"]), default=str, ensure_ascii=False)

        @beta_tool
        def read_evidence_file(path: str) -> str:
            """Read an evidence file (path relative to the data root, as listed in the finding's evidence).

            Args:
                path: e.g. "soony/erp/open_shipments.csv" or a supplier document path.
            """
            try:
                text = file_text(settings.data_root / safe_rel(path))
            except (ValueError, OSError) as exc:
                return f"cannot read {path}: {exc}"
            agent.log("read_evidence", path, [{"path": path, "locator": "read"}])
            return (text or "(no text)")[:6000]

        @beta_tool
        def send_email(subject: str, body: str, body_en: str) -> str:
            """Send ONE email to this finding's supplier (address and safety rules are applied automatically).

            Args:
                subject: Subject line in the supplier's language, with our reference number.
                body: The email text in the supplier's language.
                body_en: English translation of body, for our audit trail.
            """
            return json.dumps(agent.write_mail(ctx, subject, body, body_en), ensure_ascii=False)

        @beta_tool
        def escalate_to_buyer(reason: str) -> str:
            """Hand the matter to a human buyer instead of emailing.

            Args:
                reason: One sentence why a human must take over.
            """
            return json.dumps(agent.escalate(ctx, reason))

        client = llm._anthropic_client()
        from zoneinfo import ZoneInfo

        now = utcnow().astimezone(ZoneInfo("Europe/Berlin")).strftime("%a %d.%m.%Y %H:%M")
        prompt = (f"Clarify finding #{ctx['issue_id']}." if ctx["issue_id"]
                  else f"The launch planner asks you to contact {ctx['supplier_id']}: {ctx['ask']}")
        prompt += f"\nCurrent time (Europe/Berlin): {now}. Any reply-by time must be in the future."
        self.log("agent_start", prompt, data={"issue_id": ctx["issue_id"], "supplier_id": ctx["supplier_id"]})
        final_text = ""
        try:
            runner = client.beta.messages.tool_runner(
                model=settings.anthropic_model,
                max_tokens=16000,
                max_iterations=settings.chaser_max_steps,
                system=SYSTEM_PROMPT,
                tools=[get_finding, get_supplier_contact, get_mail_history, read_evidence_file, send_email,
                       escalate_to_buyer],
                messages=[{"role": "user", "content": prompt}],
                output_config={"effort": settings.llm_effort},
                betas=[llm.FALLBACK_BETA],
                fallbacks="default",
            )
            for message in runner:
                calls = [b.name for b in message.content if getattr(b, "type", "") == "tool_use"]
                text = "".join(b.text for b in message.content if getattr(b, "type", "") == "text").strip()
                if text:
                    final_text = text
                trace.log(self.name, "llm_call", "chaser.agent_step",
                          data={"task": "chaser.agent", "tools": calls, "stop_reason": message.stop_reason,
                                "model": message.model, "input_tokens": message.usage.input_tokens,
                                "output_tokens": message.usage.output_tokens})
                if message.stop_reason == "refusal":
                    break
        except (APIStatusError, APIConnectionError) as exc:
            raise llm.LLMError(str(exc)) from exc
        result = {"status": "escalated" if ctx["escalated"] else ("mailed" if ctx["outbox_id"] else "no_action"),
                  "outbox_id": ctx["outbox_id"], "note": final_text}
        self.log("agent_done", final_text or result["status"], data={"issue_id": ctx["issue_id"], **result})
        return result

    # -- fallback (MOCK_MODE) -------------------------------------------------
    def _fallback(self, ctx: dict) -> dict:
        issue = ctx["issue"]
        r = ctx["request"]
        po = f"{r['po_number']}/{r['po_line']}" if r.get("po_number") and r.get("po_line") else r.get("po_number")
        parts = (po, r.get("shipment_id"), r.get("part")) if r else (
            (issue.po_number, issue.shipment_id, issue.part_id) if issue else ())
        refs = " / ".join(str(x) for x in parts if x)
        question = ctx["ask"] or (issue.summary if issue else "please confirm the open point")
        contact = self.contact(ctx["supplier_id"])
        name = contact.get("contact_name") or "Sir or Madam"
        subject = f"Soony launch: please confirm {refs}".strip() if refs else "Soony launch: quick question"
        body = (f"Dear {name},\n\nfor our launch we need one confirmation regarding {refs or 'our open order'}:\n"
                f"{question}\n\nCould you please reply today? Thank you very much.\n\nBest regards\nSoony procurement")
        result = self.write_mail(ctx, subject, body, body)
        out = {"status": "mailed" if result.get("outbox_id") else "skipped", **result, "mode": "fallback"}
        fixture = _fixture_for(ctx["supplier_id"])
        if result.get("outbox_id") and fixture:
            self._deliver_reply(result["outbox_id"], ctx["supplier_id"], fixture.get("reply_body", ""),
                                subject=fixture.get("reply_subject", ""), from_email=fixture.get("reply_from", ""),
                                note=f"replayed recorded exchange {fixture['_path']}")
            out["replayed"] = fixture["_path"]
        return out

    # -- replies, escalation ----------------------------------------------------
    def _file_sent(self, row):
        try:
            mailbox.file_sent(row.to_email, row.subject, row.body, row.message_id)
        except mailbox.MailError as exc:
            self.log("sent_copy_pending", str(exc), data={"outbox_id": row.id})
        else:
            self._set_outbox(row.id, sent_copy_pending=False)

    def poll_inbox(self) -> dict:
        """Read new mails from the shared mailbox and hand them to Ingest. Only on the CHASER_SEND instance."""
        settings = get_settings()
        if llm.is_mock() or not settings.chaser_send or not mailbox.is_configured():
            return {"polled": False, "reason": "mock mode, CHASER_SEND off, or mailbox not configured"}
        try:
            with session() as s:
                pending = s.exec(select(OutboxMessage).where(OutboxMessage.sent_copy_pending == True)).all()
                sent = s.exec(select(OutboxMessage.message_id, OutboxMessage.to_email).where(
                    OutboxMessage.sent_at.is_not(None), OutboxMessage.message_id != "")).all()
            for row in pending:
                self._file_sent(row)
            mails = mailbox.fetch_replies([mid for mid, _ in sent], {address for _, address in sent})
        except mailbox.MailError as exc:
            self.log("error", f"inbox poll failed: {exc}")
            return {"polled": False, "error": str(exc)}
        matched = 0
        for m in mails:
            row = self._match(m)
            if row is None:
                self.log("unmatched_mail", f"from {m.from_email}: {m.subject}", data={"uid": m.uid})
                continue
            if not m.message_id:
                self.log("unmatched_mail", "Reply has no Message-ID", data={"uid": m.uid})
                continue
            with session() as s:
                existing = s.exec(select(InboxMessage).where(
                    InboxMessage.transport_message_id == m.message_id)).first()
            if existing:
                if not existing.processed:
                    self._set_outbox(row.id, status="replied")
                    self.emit(events.REPLY_RECEIVED, {"inbox_id": existing.id, "supplier_id": row.supplier_id,
                                                      "issue_id": row.issue_id})
                continue
            self._deliver_reply(row.id, row.supplier_id, m.body, subject=m.subject, from_email=m.from_email,
                                note="mailbox", transport_message_id=m.message_id)
            matched += 1
        escalated = self.check_silence()
        return {"polled": True, "new": len(mails), "matched": matched, "escalated": escalated}

    def _match(self, m: mailbox.IncomingMail) -> Optional[OutboxMessage]:
        parents = m.in_reply_to.split() or m.references.split()[-1:]
        with session() as s:
            if s.exec(select(OutboxMessage.id).where(OutboxMessage.message_id == m.message_id)).first():
                return None
            rows = s.exec(select(OutboxMessage).where(OutboxMessage.message_id.in_(parents))).all()
            return rows[0] if len(rows) == 1 else None

    def _deliver_reply(self, outbox_id: int, supplier_id: str, body: str, *, subject: str = "",
                       from_email: str = "", note: str = "", transport_message_id=None) -> None:
        from .. import system

        with session() as s:
            row = s.get(OutboxMessage, outbox_id)
            issue_id, language = (row.issue_id, row.language) if row else (None, "")
        msg = system.store_reply(supplier_id=supplier_id, body=body, issue_id=issue_id, outbox_id=outbox_id,
                                 from_email=from_email, subject=subject, language=language, source=self.name,
                                 transport_message_id=transport_message_id)
        self._set_outbox(outbox_id, status="replied")
        self.log("reply_received", f"#{outbox_id} answered by {supplier_id} ({note})",
                 data={"outbox_id": outbox_id, "inbox_id": msg.id, "issue_id": issue_id})
        self.emit(events.REPLY_RECEIVED, {"inbox_id": msg.id, "supplier_id": supplier_id, "issue_id": issue_id})

    def check_silence(self) -> int:
        """T4.6: mails without reply after CHASER_ESCALATE_H hours -> human_check finding."""
        limit = utcnow() - timedelta(hours=get_settings().chaser_escalate_h)
        with session() as s:
            rows = s.exec(select(OutboxMessage).where(OutboxMessage.status == "sent")).all()
        count = 0
        for row in rows:
            if row.sent_at and _aware(row.sent_at) < limit:
                issue = None
                if row.issue_id:
                    with session() as s:
                        issue = s.get(Issue, row.issue_id)
                self.escalate({"issue": issue, "issue_id": row.issue_id, "supplier_id": row.supplier_id,
                               "decision_id": row.decision_id},
                              f"{row.supplier_id} has not answered mail #{row.id} for "
                              f"{get_settings().chaser_escalate_h:g}h")
                self._set_outbox(row.id, status="escalated")
                count += 1
        return count

    def export_fixture(self, outbox_id: int) -> Path:
        """Save a REAL exchange (sent mail + reply) as the demo fallback in backend/fixtures/chaser/."""
        with session() as s:
            out = s.get(OutboxMessage, outbox_id)
            reply = s.exec(select(InboxMessage).where(InboxMessage.outbox_id == outbox_id)
                           .order_by(InboxMessage.id.desc())).first()
        if out is None or reply is None:
            raise ValueError(f"outbox #{outbox_id} has no stored reply yet")
        FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
        path = FIXTURE_DIR / f"{out.supplier_id}_outbox{outbox_id}.json"
        path.write_text(json.dumps({
            "supplier_id": out.supplier_id, "recorded_at": str(reply.received_at), "language": out.language,
            "sent_subject": out.subject, "sent_body": out.body, "sent_body_en": out.body_en,
            "reply_subject": reply.subject, "reply_body": reply.body, "reply_from": reply.from_email,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    # -- tiny db helpers ----------------------------------------------------------
    def _set_outbox(self, outbox_id: int, **values: Any) -> None:
        with session() as s:
            row = s.get(OutboxMessage, outbox_id)
            if row:
                for k, v in values.items():
                    setattr(row, k, v)
                s.add(row)
                s.commit()

    def _set_issue_status(self, issue_id: int, status: str) -> None:
        with session() as s:
            issue = s.get(Issue, issue_id)
            if issue and issue.status not in ("resolved",):
                issue.status, issue.updated_at = status, utcnow()
                s.add(issue)
                s.commit()


def _aware(dt):
    """SQLite returns naive datetimes; utcnow() is aware. Treat naive values as UTC."""
    from datetime import timezone

    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


if __name__ == "__main__":  # python -m backend.agents.chaser export <outbox_id> | poll
    import sys

    from .. import system

    system.bus()
    chaser = next(a for a in system._agents if a.name == "chaser")
    if len(sys.argv) >= 3 and sys.argv[1] == "export":
        print(chaser.export_fixture(int(sys.argv[2])))
    elif len(sys.argv) >= 2 and sys.argv[1] == "poll":
        print(chaser.poll_inbox())
    else:
        print(__doc__)
