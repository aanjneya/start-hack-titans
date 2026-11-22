"""Chaser (T4): contact lookup, anti-spam, fallback mail, recorded-reply replay, reply matching, escalation.

Runs in MOCK mode (conftest), so no Claude calls and no real mails.
"""
import json
from datetime import timedelta

from sqlmodel import select

from backend import system
from backend.agents import chaser as chaser_mod
from backend.agents.base import issue_fingerprint
from backend.core import mail as mailbox
from backend.core.models import InboxMessage, Issue, IssueKind, OutboxMessage, utcnow
from backend.core.store import session


def _chaser():
    system.bus()
    return next(a for a in system._agents if a.name == "chaser")


def _raise(ch, key="SHP-TEST/1", supplier="SUP-0028"):
    issue = ch.raise_issue(
        type="test_missing_eta", kind=IssueKind.MISSING_INFO, severity=3, key=key,
        summary="Shipment has no ETA", supplier_id=supplier, shipment_id="SHP-2026-04276",
        suggested_actions=[{"type": "email", "label": "Ask for ETA", "target": supplier, "owner": "chaser"}],
        evidence=[{"path": "soony/erp/open_shipments.csv", "locator": "SHP-2026-04276"}])
    system.bus().run_until_idle()
    return issue


def _outbox():
    with session() as s:
        return s.exec(select(OutboxMessage).order_by(OutboxMessage.id)).all()


def test_contact_comes_from_supplier_profile():
    c = _chaser().contact("SUP-0028")
    assert c["email"].startswith("a.nowak.karton-nowak@")
    assert c["language"] == "pl"
    c29 = _chaser().contact("SUP-0029")  # JSON (SAP export) profile format
    assert c29["email"] and c29["language"]


def test_missing_info_finding_creates_one_mail_and_marks_asked():
    ch = _chaser()
    issue = _raise(ch)
    rows = _outbox()
    assert len(rows) == 1
    assert rows[0].status == "sent"  # mock mode: delivery simulated, nothing really sent
    assert rows[0].to_email.startswith("a.nowak.karton-nowak@")
    assert "SHP-2026-04276" in rows[0].subject
    with session() as s:
        assert s.get(Issue, issue.id).status == "asked"


def test_anti_spam_one_open_question_per_supplier():
    ch = _chaser()
    _raise(ch, key="A")
    _raise(ch, key="B")  # same supplier, different problem
    assert len(_outbox()) == 1
    assert "still open" in ch.blocked_reason("SUP-0028")


def test_min_gap_after_reply():
    ch = _chaser()
    _raise(ch, key="A")
    ch._set_outbox(_outbox()[0].id, status="replied", sent_at=utcnow())
    assert "less than" in ch.blocked_reason("SUP-0028")


def test_recorded_real_reply_is_replayed_in_fallback(tmp_path, monkeypatch):
    monkeypatch.setattr(chaser_mod, "FIXTURE_DIR", tmp_path)
    (tmp_path / "SUP-0028_outbox1.json").write_text(json.dumps(
        {"request_scope": {"finding": issue_fingerprint("test_missing_eta", "SHP-TEST/1")},
         "reply_subject": "Re: test", "reply_body": "ETA 22.11. 08:00", "reply_from": "x@example.com"}))
    ch = _chaser()
    _raise(ch)
    assert _outbox()[0].status == "replied"
    with session() as s:
        inbox = s.exec(select(InboxMessage)).all()
    assert len(inbox) == 1 and inbox[0].body == "ETA 22.11. 08:00" and inbox[0].outbox_id == _outbox()[0].id


def test_reply_is_matched_by_in_reply_to():
    ch = _chaser()
    _raise(ch)
    row = _outbox()[0]
    mail = mailbox.IncomingMail(uid="1", message_id="<r@x>", in_reply_to=row.message_id, references="",
                                from_email="someone-else@example.com", subject="Re", body="ok")
    assert ch._match(mail).id == row.id


def test_silence_escalates_to_a_buyer():
    ch = _chaser()
    _raise(ch)
    row = _outbox()[0]
    ch._set_outbox(row.id, status="sent", sent_at=utcnow() - timedelta(hours=10))
    assert ch.check_silence() == 1
    assert _outbox()[0].status == "escalated"
    with session() as s:
        esc = s.exec(select(Issue).where(Issue.type == "chaser_escalation")).all()
    assert len(esc) == 1 and esc[0].kind == IssueKind.HUMAN_CHECK


def test_finding_without_supplier_is_skipped():
    ch = _chaser()
    ch.raise_issue(type="test_missing", kind=IssueKind.MISSING_INFO, severity=2, key="nosup",
                   summary="Unknown sender", evidence=[{"path": "soony/erp/open_shipments.csv"}])
    system.bus().run_until_idle()
    assert _outbox() == []


def _request(decision_id, supplier="SUP-0028"):
    from backend.core import events

    system.bus().emit(events.EMAIL_REQUESTED, {
        "supplier_id": supplier, "decision_id": decision_id, "issue_id": None, "po_number": "4500318792",
        "po_line": 1, "shipment_id": "SHP-2026-04276", "part": "CMP-GAM-004",
        "ask": "Confirm released revision, quantity and timed site delivery for PO 4500318792/1."}, source="planner")
    system.bus().run_until_idle()


def test_planner_request_one_mail_per_decision_keeps_decision_id():
    _chaser()
    _request(41)
    _request(41)  # planner re-emits: no second mail
    rows = [r for r in _outbox() if r.decision_id == 41]
    assert len(rows) == 1
    assert rows[0].status in ("sent", "replied")  # Planner marks the decision executed on sent/replied
    assert "4500318792/1" in rows[0].subject


def test_approved_decision_is_not_blocked_by_open_question():
    ch = _chaser()
    _raise(ch)  # open question to SUP-0028
    assert ch.blocked_reason("SUP-0028")
    _request(42)
    assert any(r.decision_id == 42 for r in _outbox())
    assert "still open" in ch.blocked_reason("SUP-0028")  # questions stay limited


def test_site_and_customer_from_setup_without_cross_thread_fixture(tmp_path, monkeypatch):
    from dataclasses import replace
    from backend.core import config, policy

    custom = tmp_path / "acme.json"
    custom.write_text(json.dumps({"customer": "Kobe Tools", "timezone": "Asia/Tokyo"}))
    monkeypatch.setenv("CUSTOMER_SETUP", str(custom))
    config.reset_settings()
    policy.reset_cache()
    monkeypatch.setattr(chaser_mod, "FIXTURE_DIR", tmp_path)
    legacy = {"supplier_id": "SUP-0028", "reply_subject": "Re: Another order", "reply_body": "Unrelated response"}
    (tmp_path / "SUP-0028_outbox2.json").write_text(json.dumps(legacy))
    ch = _chaser()
    _raise(ch)
    mail, = _outbox()
    assert mail.subject.startswith("Kobe Tools") and "Kobe Tools procurement" in mail.body and "Soony" not in mail.body
    with session() as db:
        assert not db.exec(select(InboxMessage)).all()
    legacy.update(sent_subject=mail.subject, sent_body=mail.body)
    (tmp_path / "SUP-0028_outbox2.json").write_text(json.dumps(legacy))
    assert chaser_mod._fixture_for("SUP-0028", None, mail.subject, mail.body)
    monkeypatch.setattr(chaser_mod, "get_settings", lambda: replace(config.get_settings(), mock_mode=False))
    assert chaser_mod._fixture_for("SUP-0028", None, mail.subject, mail.body) is None
