"""Replay the real portal drops in order (T0.8): proves "a new drop arrives -> no code change".

Every 15-min check commits the portal mirror to git (challenge/start-hack-data/data/, earlier
start-hack-data/data/). Each commit that changed data files = one drop. We rebuild the folder as it
was after each drop (git archive) and send the changed files to the agents.

CLI (from repo root):
  python -m backend.core.replay --list        # show the drops found in git history
  python -m backend.core.replay               # reset DB, replay all drops through all agents
  python -m backend.core.replay --upto 2      # only the first N drops
"""
from __future__ import annotations

import argparse
import io
import shutil
import subprocess
import tarfile
from dataclasses import dataclass
from pathlib import Path

from .config import REPO_ROOT, get_settings

DATA_PREFIXES = ("challenge/start-hack-data/data/", "start-hack-data/data/")


@dataclass
class Drop:
    index: int
    commit: str
    label: str  # commit subject, e.g. "data: Lio check 2026-11-21T16-08 – 5 added …"
    prefix: str
    added: list[str]
    changed: list[str]
    removed: list[str]


def _git(*args: str) -> str:
    return subprocess.run(["git", "-C", str(REPO_ROOT), *args], check=True, capture_output=True, text=True).stdout


def _strip(path: str) -> tuple[str, str] | None:
    for prefix in DATA_PREFIXES:
        if path.startswith(prefix):
            return prefix, path[len(prefix):]
    return None


def list_drops() -> list[Drop]:
    """Commits (oldest first) that added/changed/removed portal data files."""
    log = _git("log", "--reverse", "--format=%H%x09%s", "--", *[p.rstrip("/") for p in DATA_PREFIXES])
    drops: list[Drop] = []
    for line in log.splitlines():
        commit, _, subject = line.partition("\t")
        diff = _git("diff-tree", "--root", "--no-commit-id", "-r", "-M", "--name-status", commit)
        added, changed, removed, prefix = [], [], [], ""
        for row in diff.splitlines():
            parts = row.split("\t")
            status = parts[0]
            if status.startswith("R"):
                old, new = _strip(parts[1]), _strip(parts[2])
                # a pure move of the folder (start-hack-data/ -> challenge/start-hack-data/) is not a drop
                if old and new and old[1] == new[1]:
                    continue
                if old:
                    removed.append(old[1])
                if new:
                    prefix = new[0]
                    added.append(new[1])
                continue
            hit = _strip(parts[-1])
            if not hit:
                continue
            prefix = hit[0]
            {"A": added, "M": changed, "D": removed}.get(status[0], changed).append(hit[1])
        if added or changed or removed:
            drops.append(Drop(len(drops), commit, subject, prefix, sorted(added), sorted(changed), sorted(removed)))
    return drops


def materialize(drop: Drop, dest: Path | None = None) -> Path:
    """Write the portal data folder exactly as it was after `drop` into dest; return that path."""
    dest = dest or get_settings().replay_dir / f"{drop.index:03d}"
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    tar_bytes = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "archive", "--format=tar", drop.commit, drop.prefix.rstrip("/")],
        check=True, capture_output=True,
    ).stdout
    with tarfile.open(fileobj=io.BytesIO(tar_bytes)) as tar:
        for member in tar.getmembers():
            if not member.isfile() or not member.name.startswith(drop.prefix):
                continue
            rel = member.name[len(drop.prefix):]
            if not rel or rel.startswith("/") or ".." in Path(rel).parts:
                continue
            target = dest / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            extracted = tar.extractfile(member)
            if extracted is not None:
                target.write_bytes(extracted.read())
    return dest


def replay(upto: int | None = None, reset: bool = True) -> list[dict]:
    """Reset the DB and push every drop through the agents in order. Returns a summary per drop."""
    from .. import system  # local import: system imports agents

    if reset:
        system.reset()
    results = []
    for drop in list_drops()[: upto if upto is not None else None]:
        root = materialize(drop)
        results.append(
            system.process_drop(
                added=drop.added, changed=drop.changed, removed=drop.removed,
                label=drop.label, data_root=root, mode="replay",
            )
        )
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="Replay portal drops from git history")
    parser.add_argument("--list", action="store_true", help="only list the drops")
    parser.add_argument("--upto", type=int, default=None, help="replay only the first N drops")
    args = parser.parse_args()
    if args.list:
        for d in list_drops():
            print(f"{d.index:3d}  {d.commit[:8]}  +{len(d.added)} ~{len(d.changed)} -{len(d.removed)}  {d.label}")
        return
    for r in replay(upto=args.upto):
        print(r)


if __name__ == "__main__":
    main()
