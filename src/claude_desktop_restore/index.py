"""Reading and writing the session index.

Everything that touches disk lives here, so the rules that must never be broken
are enforceable in one place:

- transcripts under ``~/.claude/projects/`` are **only ever read**;
- records are written UTF-8, minified, with **no BOM** (a BOM makes the app's
  parser reject the record outright) and no trailing newline;
- deletion tombstones are honoured, or a rebuild resurrects every session the
  user ever deleted.
"""

from __future__ import annotations

import glob
import json
import os
import shutil
from datetime import datetime
from typing import Any

MANIFEST = ".restore-manifest.json"

TOMBSTONE_PREFIX = "deleted_"


def load_manifest(account_dir: str) -> set[str]:
    """Session ids this tool wrote, so they are excluded from the reference pool."""
    path = os.path.join(account_dir, MANIFEST)
    if not os.path.isfile(path):
        return set()
    try:
        with open(path, encoding="utf-8-sig") as f:
            return set(json.load(f))
    except Exception:
        return set()


def save_manifest(account_dir: str, session_ids: set[str]) -> None:
    write_json(os.path.join(account_dir, MANIFEST), sorted(session_ids))


def read_records(account_dir: str) -> list[dict[str, Any]]:
    """Every ``local_*.json`` in the folder, skipping unparseable ones."""
    records = []
    for path in sorted(glob.glob(os.path.join(account_dir, "local_*.json"))):
        try:
            with open(path, encoding="utf-8-sig") as f:
                records.append(json.load(f))
        except Exception:
            continue
    return records


def split_records(
    account_dir: str, authored: set[str]
) -> tuple[dict[str, str], list[dict[str, Any]]]:
    """Returns ``(cliSessionId -> filename, app_written_records)``."""
    existing: dict[str, str] = {}
    app_written: list[dict[str, Any]] = []
    for path in sorted(glob.glob(os.path.join(account_dir, "local_*.json"))):
        try:
            with open(path, encoding="utf-8-sig") as f:
                record = json.load(f)
        except Exception:
            continue
        if record.get("cliSessionId"):
            existing[record["cliSessionId"]] = os.path.basename(path)
        if record.get("sessionId") not in authored:
            app_written.append(record)
    return existing, app_written


def find_tombstones(account_dir: str) -> set[str]:
    """The uuids in ``deleted_*`` files.

    Deleting a session in the UI writes a *pair* -- ``deleted_<sessionId>`` and
    ``deleted_<cliSessionId>`` -- each holding the same deletion time in epoch
    milliseconds. 78 tombstones on the machine tested resolved into exactly 39
    pairs, no singletons.

    The ``local_*.json`` is removed at the same time but the transcript is not,
    which is why a deleted session is otherwise indistinguishable from a
    never-indexed one. None of the four prior tools in this space check for
    these, so all of them resurrect deleted sessions on a rerun.
    """
    return {
        os.path.basename(p)[len(TOMBSTONE_PREFIX) :]
        for p in glob.glob(os.path.join(account_dir, TOMBSTONE_PREFIX + "*"))
    }


def find_transcripts(projects_root: str) -> list[str]:
    """Top-level ``<project>/<uuid>.jsonl`` only.

    Anything deeper is a subagent transcript or a workflow journal. On two real
    machines that is the difference between 60 sessions and 233 files, and
    between 113 and 329.
    """
    return sorted(glob.glob(os.path.join(projects_root, "*", "*.jsonl")))


def backup_index(index_root: str) -> str:
    """Copy the whole index alongside itself. Returns the backup path."""
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = os.path.join(
        os.path.dirname(index_root), f"claude-code-sessions_backup_{stamp}"
    )
    shutil.copytree(index_root, backup)
    return backup


def write_json(path: str, payload: Any) -> None:
    """UTF-8, minified, no BOM, no trailing newline.

    The app's JSON parser rejects a record carrying a BOM, so this must never go
    through an encoding that adds one -- ``utf-8-sig`` is correct for *reading*
    and wrong for writing.
    """
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(data)


def write_records(account_dir: str, records: list[dict[str, Any]]) -> list[str]:
    """Write one file per record. Returns the paths written."""
    written = []
    for record in records:
        path = os.path.join(account_dir, record["sessionId"] + ".json")
        write_json(path, record)
        written.append(path)
    return written
