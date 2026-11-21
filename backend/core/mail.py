"""Shared mailbox for the Chaser: send via SMTP, read replies via IMAP.

All settings come from env/.env (MAIL_ADDRESS, MAIL_PASSWORD, SMTP_*, IMAP_*); nothing is hardcoded.
Only the ONE instance with CHASER_SEND=true should ever call these functions (see .brain/tasks/4-chaser.md).
"""
from __future__ import annotations

import email
import imaplib
import smtplib
import ssl
from dataclasses import dataclass
from email.header import decode_header, make_header
from email.message import EmailMessage
from email.utils import make_msgid, parseaddr

from .config import get_settings


class MailError(RuntimeError):
    pass


@dataclass
class IncomingMail:
    uid: str
    message_id: str
    in_reply_to: str
    references: str
    from_email: str
    subject: str
    body: str


def is_configured() -> bool:
    s = get_settings()
    return bool(s.mail_address and s.mail_password and s.smtp_host and s.imap_host)


def new_message_id() -> str:
    domain = (get_settings().mail_address.split("@", 1)[-1] or "localhost").strip()
    return make_msgid(idstring="chaser", domain=domain)


def send(to: str, subject: str, body: str, *, message_id: str, in_reply_to: str = "") -> None:
    """Send one plain-text mail. Raises MailError on any SMTP problem."""
    s = get_settings()
    if not is_configured():
        raise MailError("mailbox not configured (MAIL_ADDRESS / MAIL_PASSWORD / SMTP_HOST / IMAP_HOST)")
    msg = EmailMessage()
    msg["From"] = s.mail_address
    msg["To"] = to
    msg["Subject"] = subject
    msg["Message-ID"] = message_id
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
        msg["References"] = in_reply_to
    msg.set_content(body)
    ctx = ssl.create_default_context()
    try:
        if s.smtp_security == "ssl":
            with smtplib.SMTP_SSL(s.smtp_host, s.smtp_port, context=ctx, timeout=30) as smtp:
                smtp.login(s.mail_address, s.mail_password)
                smtp.send_message(msg)
        else:
            with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=30) as smtp:
                smtp.starttls(context=ctx)
                smtp.login(s.mail_address, s.mail_password)
                smtp.send_message(msg)
    except (smtplib.SMTPException, OSError) as exc:
        raise MailError(f"{type(exc).__name__}: {exc}") from exc


def _text(part_or_msg) -> str:
    if part_or_msg.is_multipart():
        for part in part_or_msg.walk():
            if part.get_content_type() == "text/plain" and "attachment" not in str(part.get("Content-Disposition", "")):
                return _decode_payload(part)
        for part in part_or_msg.walk():  # html-only mail: crude fallback
            if part.get_content_type() == "text/html":
                return _decode_payload(part)
        return ""
    return _decode_payload(part_or_msg)


def _decode_payload(part) -> str:
    raw = part.get_payload(decode=True) or b""
    charset = part.get_content_charset() or "utf-8"
    try:
        return raw.decode(charset, errors="replace")
    except LookupError:
        return raw.decode("utf-8", errors="replace")


def _header(msg, name: str) -> str:
    value = msg.get(name, "")
    try:
        return str(make_header(decode_header(value))) if value else ""
    except Exception:
        return str(value)


def fetch_unseen(mark_seen: bool = True) -> list[IncomingMail]:
    """All unread mails in INBOX. Marks them seen so the next poll doesn't return them again."""
    s = get_settings()
    if not is_configured():
        raise MailError("mailbox not configured")
    out: list[IncomingMail] = []
    try:
        box = imaplib.IMAP4_SSL(s.imap_host, s.imap_port, ssl_context=ssl.create_default_context(), timeout=30)
        box.login(s.mail_address, s.mail_password)
        box.select("INBOX", readonly=not mark_seen)
        typ, data = box.uid("search", None, "UNSEEN")
        uids = data[0].split() if typ == "OK" and data and data[0] else []
        for uid in uids:
            typ, parts = box.uid("fetch", uid, "(RFC822)")
            if typ != "OK" or not parts or not isinstance(parts[0], tuple):
                continue
            msg = email.message_from_bytes(parts[0][1])
            out.append(IncomingMail(
                uid=uid.decode(),
                message_id=_header(msg, "Message-ID").strip(),
                in_reply_to=_header(msg, "In-Reply-To").strip(),
                references=_header(msg, "References").strip(),
                from_email=parseaddr(_header(msg, "From"))[1].lower(),
                subject=_header(msg, "Subject"),
                body=_text(msg).strip(),
            ))
            if mark_seen:
                box.uid("store", uid, "+FLAGS", "(\\Seen)")
        box.logout()
    except (imaplib.IMAP4.error, OSError) as exc:
        raise MailError(f"{type(exc).__name__}: {exc}") from exc
    return out
