"""Watcher agent (owner: daksh, .brain/tasks/1-watcher.md).

Notices what changed on the Soony portal and hands exactly those files to the Ingest agent. No AI.

- Live (MOCK_MODE=false + SOONY_API_TOKEN): GET /api/v1/files (path + SHA-256 per file), compare with the manifest
  in `portal_files`, download only added/changed files into PORTAL_MIRROR, then system.process_drop(...).
  Runs on a timer inside the backend when WATCH_INTERVAL_MIN > 0 (T1.5), or via POST /api/watch/check.
- Offline / demo: the real drops are replayed from git history (core/replay.py, POST /api/replay/step).
"""
from __future__ import annotations

import hashlib
import json
import ssl
import urllib.parse
import urllib.request
from pathlib import Path

from sqlmodel import select

from ..core import events, trace
from ..core.config import get_settings
from ..core.models import PortalFile, utcnow
from ..core.parsing import safe_rel
from ..core.store import session
from .base import Agent, register


def _ssl_ctx():
    for ca in ("/etc/ssl/cert.pem", "/private/etc/ssl/cert.pem"):
        if Path(ca).exists():
            return ssl.create_default_context(cafile=ca)
    return ssl.create_default_context()


@register
class Watcher(Agent):
    name = "watcher"
    subscriptions = {}  # triggered by timer / API / replay, not by events

    def _get(self, url: str) -> bytes:
        s = get_settings()
        target = urllib.parse.urljoin(s.soony_base_url.rstrip("/") + "/", url)
        endpoint, portal = urllib.parse.urlsplit(target), urllib.parse.urlsplit(s.soony_base_url)
        if endpoint.scheme not in {"https", "http"}:
            raise ValueError("Expected an HTTP portal download.")
        req = urllib.request.Request(target)
        if (endpoint.scheme, endpoint.netloc) == (portal.scheme, portal.netloc):
            req.add_unredirected_header("Authorization", f"Bearer {s.soony_api_token}")
        with urllib.request.urlopen(req, timeout=60, context=_ssl_ctx()) as r:
            return r.read()

    def list_portal(self) -> list[dict]:
        """[{path, bytes, sha256, url}] from the portal API."""
        s = get_settings()
        return json.loads(self._get(f"{s.soony_base_url}/api/v1/files"))["files"]

    def diff(self, files: list[dict]) -> tuple[list[dict], list[dict], list[str]]:
        """Compare the portal list with our manifest -> (added, changed, removed paths)."""
        with session() as s:
            known = {f.path: f for f in s.exec(select(PortalFile)).all() if not f.removed}
        now = {f["path"]: f for f in files}
        added = [f for p, f in now.items() if p not in known]
        changed = [f for p, f in now.items() if p in known and known[p].sha256 != f["sha256"]]
        removed = [p for p in known if p not in now]
        return added, changed, removed

    def check(self) -> dict:
        """One live check (T1.1–T1.4). Returns a summary; in MOCK mode or without a token it does nothing."""
        s = get_settings()
        run_id = trace.new_run_id()
        if s.mock_mode or not (s.soony_api_token and s.soony_base_url):
            self.log("skip", "live portal check skipped (MOCK_MODE or no SOONY_API_TOKEN); use the replay")
            return {"skipped": True}
        files = self.list_portal()
        added, changed, removed = self.diff(files)
        mirror = s.portal_mirror
        for f in added + changed:
            rel = safe_rel(f["path"])
            url = f.get("url") or f"{s.soony_base_url}/api/v1/files/{urllib.parse.quote(f['path'])}"
            data = self._get(url)
            if hashlib.sha256(data).hexdigest() != f["sha256"]:
                self.log("error", f"checksum mismatch for {f['path']}", [{"path": f["path"]}])
                raise ValueError(f"checksum mismatch for {rel}")
            (mirror / rel).parent.mkdir(parents=True, exist_ok=True)
            (mirror / rel).write_bytes(data)
        label = f"portal check +{len(added)} ~{len(changed)} -{len(removed)}"
        self.log("check", label, data={"added": len(added), "changed": len(changed), "removed": len(removed)})
        if not (added or changed or removed):
            return {"added": 0, "changed": 0, "removed": 0}
        from .. import system  # local import: system imports the agents

        result = system.process_drop(added=[f["path"] for f in added], changed=[f["path"] for f in changed],
                                   removed=removed, label=label, data_root=mirror, mode="live", watcher_run=run_id)

        if result["status"] != "completed":
            # Keep the last successful manifest so the next poll delivers these files again.
            return result

        # Advance the manifest only once downstream processing has succeeded.
        with session() as db:
            for f in added + changed:
                rows = db.exec(select(PortalFile).where(PortalFile.path == f["path"])).all()
                row = rows[-1] if rows else PortalFile(path=f["path"])
                for previous in rows[:-1]:
                    previous.removed = True
                    db.add(previous)
                row.sha256, row.bytes, row.removed, row.seen_at = f["sha256"], f.get("bytes"), False, utcnow()
                db.add(row)
            for path in removed:
                for row in db.exec(select(PortalFile).where(PortalFile.path == path)).all():
                    row.removed, row.seen_at = True, utcnow()
                    db.add(row)
            db.commit()
        return result

    def replay_manifest(self, payload):
        """Hand the actual Git manifest to the same subscribers used after a live portal diff."""
        self.log("replay_manifest", f"Read historical portal drop: +{len(payload['added'])} files, "
                 f"{len(payload['changed'])} changed, {len(payload['removed'])} removed.",
                 data={"drop_id": payload["drop_id"]})
        self.emit(events.FILES_CHANGED, payload)
