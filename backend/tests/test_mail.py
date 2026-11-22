"""GMX can find the original Message-ID when asked to search In-Reply-To headers."""
from contextlib import contextmanager

from backend.core import mail


def test_read_only_poll_validates_headers_before_fetching_body(monkeypatch):
    ids = [b"<out@test>", b"<reply@test>", b"<other@test>"]
    class Box:
        def list(self):
            return "OK", [b'(\\Junk) "/" "Spam folder"']

        def select(self, folder, readonly):
            assert readonly
            self.folder = folder
            return "OK", []

        def uid(self, command, *args):
            if command == "search":
                if self.folder == b"INBOX":
                    return "OK", [b"1 3" if "FROM" in args else b"1"]
                return "OK", [b"2"]
            uid, query = args
            header = b"Message-ID: " + ids[int(uid)-1] + b"\r\n"
            if uid == b"2":
                header += b"In-Reply-To: <out@test>\r\n"
            assert command == "fetch" and "BODY.PEEK" in query
            if query == "(BODY.PEEK[])":
                assert uid == b"2"
            return "OK", [(b"", header + b"\r\nTest confirmation")]

    @contextmanager
    def connection():
        yield Box()
    monkeypatch.setattr(mail, "_connection", connection)
    reply, = mail.fetch_replies(["<out@test>"], {"test@example.org"})
    assert reply.message_id == "<reply@test>" and reply.body == "Test confirmation"


def test_sent_retry_uses_existing_copy(monkeypatch):
    class Box:
        def list(self):
            return "OK", [b'(\\Sent) "/" Gesendet']
        def select(self, folder, readonly):
            assert readonly and folder == b"Gesendet"
            return "OK", []
        def uid(self, *args):
            assert args == ("search", None, "HEADER", "Message-ID", "<out@test>")
            return "OK", [b"12"]
    @contextmanager
    def connection():
        yield Box()
    monkeypatch.setattr(mail, "_connection", connection)
    mail.file_sent("buyer@example.org", "Test", "Test", "<out@test>")
