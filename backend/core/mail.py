"""Shared mailbox for the Chaser: send via SMTP, read replies via IMAP.

All settings come from env/.env (MAIL_ADDRESS, MAIL_PASSWORD, SMTP_*, IMAP_*); nothing is hardcoded.
Only the ONE instance with CHASER_SEND=true should ever call these functions (see .brain/tasks/4-chaser.md).
"""
from __future__ import annotations

import email
import imaplib
import re
import smtplib
import ssl
from contextlib import contextmanager
from dataclasses import dataclass
from email.header import decode_header, make_header
from email.message import EmailMessage
from email.utils import make_msgid, parseaddr

import certifi

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


def _message(to, subject, body, message_id, in_reply_to=""):
    s = get_settings()
    msg = EmailMessage()
    msg["From"] = s.mail_address
    msg["To"] = to
    msg["Subject"] = subject
    msg["Message-ID"] = message_id
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
        msg["References"] = in_reply_to
    msg.set_content(body)
    return msg


def _tls():
    ctx = ssl.create_default_context()
    ctx.load_verify_locations(certifi.where())
    return ctx


def send(to: str, subject: str, body: str, *, message_id: str, in_reply_to: str = "") -> None:
    s = get_settings()
    if not is_configured():
        raise MailError("mailbox not configured")
    msg = _message(to, subject, body, message_id, in_reply_to)
    ctx = _tls()
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


@contextmanager
def _connection():
    s = get_settings()
    try:
        with imaplib.IMAP4_SSL(s.imap_host, s.imap_port, ssl_context=_tls(), timeout=30) as box:
            box.login(s.mail_address, s.mail_password)
            yield box
    except (imaplib.IMAP4.error, OSError) as exc:
        raise MailError(f"{type(exc).__name__}: {exc}") from exc


def _special_folder(box, flag):
    status, rows = box.list()
    for row in rows if status == "OK" else ():
        if not isinstance(row, bytes):
            continue
        match = re.match(rb'^\(([^)]*)\)\s+(?:"[^"]*"|NIL)\s+(.+)$', row)
        if match and flag.lower() in match[1].lower().split():
            return match[2]
    return None


def file_sent(to, subject, body, message_id):
    """File an accepted SMTP message once, including on providers that auto-file it."""
    with _connection() as box:
        folder = _special_folder(box, b"\\Sent")
        if not folder or box.select(folder, readonly=True)[0] != "OK":
            raise MailError("Sent folder unavailable")
        status, ids = box.uid("search", None, "HEADER", "Message-ID", message_id)
        if status != "OK":
            raise MailError("Sent folder search failed")
        if not ids[0]:
            from email.policy import SMTP
            msg = _message(to, subject, body, message_id)
            if box.append(folder, r"(\Seen)", None, msg.as_bytes(policy=SMTP))[0] != "OK":
                raise MailError("Could not file Sent copy")


def fetch_replies(message_ids: list[str], contacts: set[str]) -> list[IncomingMail]:
    """Read only messages referencing our outbox. Works even if the buyer read them first."""
    if not message_ids:
        return []
    out = []
    with _connection() as box:
        for folder in filter(None, [b"INBOX", _special_folder(box, b"\\Junk")]):
            if box.select(folder, readonly=True)[0] != "OK":
                raise MailError("Could not open reply folder")
            uids = set()
            for mid in message_ids:
                status, data = box.uid("search", None, "OR", "HEADER", "In-Reply-To", mid,
                                       "HEADER", "References", mid)
                if status != "OK":
                    raise MailError("Reply search failed")
                uids.update(data[0].split())
            # Some providers index Message-ID for arbitrary HEADER searches. FROM supplies
            # candidates there. Verify the actual thread headers before opening any body.
            for address in contacts:
                if not re.fullmatch(r"[a-zA-Z0-9_.+@-]+", address):
                    continue
                status, data = box.uid("search", None, "FROM", address)
                if status != "OK":
                    raise MailError("Contact search failed")
                uids.update(data[0].split())
            for uid in sorted(uids, key=int):
                status, headers = box.uid("fetch", uid, "(BODY.PEEK[HEADER.FIELDS (MESSAGE-ID IN-REPLY-TO REFERENCES)])")
                if status != "OK" or not headers or not isinstance(headers[0], tuple):
                    continue
                header = email.message_from_bytes(headers[0][1])
                parents = _header(header, "In-Reply-To").split() or _header(header, "References").split()[-1:]
                if _header(header, "Message-ID").strip() in message_ids or not set(parents).intersection(message_ids):
                    continue
                status, parts = box.uid("fetch", uid, "(BODY.PEEK[])")
                if status != "OK" or not parts or not isinstance(parts[0], tuple):
                    raise MailError("Could not fetch matched reply")
                msg = email.message_from_bytes(parts[0][1])
                out.append(IncomingMail(uid.decode(), _header(msg, "Message-ID").strip(),
                                        _header(msg, "In-Reply-To").strip(), _header(msg, "References").strip(),
                                        parseaddr(_header(msg, "From"))[1].lower(), _header(msg, "Subject"),
                                        _text(msg).strip()))
    return out
