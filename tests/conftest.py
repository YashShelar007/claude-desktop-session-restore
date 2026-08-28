"""Fixtures.

Every test runs against a synthetic index and transcript tree in a temp dir.
Nothing here reads or writes the machine's real Claude state -- tests that do
are marked ``real`` and excluded by default (see pyproject).
"""

from __future__ import annotations

import json
import os
from typing import Any

import pytest

ACCOUNT = "11111111-1111-4111-8111-111111111111"
ORG = "22222222-2222-4222-8222-222222222222"

# A minimal app-written record, shaped like the real thing.
BASE_RECORD: dict[str, Any] = {
    "sessionId": "local_aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
    "cliSessionId": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
    "cwd": "/repo",
    "originCwd": "/repo",
    "lastFocusedAt": 1700000002000,
    "createdAt": 1700000000000,
    "lastActivityAt": 1700000001000,
    "model": "claude-opus-5",
    "effort": "high",
    "isArchived": False,
    "title": "Greeting",
    "titleSource": "auto",
    "permissionMode": "auto",
    "remoteMcpServersConfig": [],
    "chromePermissionMode": "skip_all_permission_checks",
    "completedTurns": 1,
    "alwaysAllowedReasons": [],
    "sessionPermissionUpdates": [],
    "classifierSummaryEnabled": True,
    "spawnSeed": {},
}


def write_json(path: str, payload: Any) -> None:
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))


@pytest.fixture
def index_root(tmp_path) -> str:
    """An index root with one <account>/<org> folder and no records yet."""
    root = tmp_path / "claude-code-sessions"
    (root / ACCOUNT / ORG).mkdir(parents=True)
    return str(root)


@pytest.fixture
def account_dir(index_root) -> str:
    return os.path.join(index_root, ACCOUNT, ORG)


@pytest.fixture
def make_record(account_dir):
    """Write an app-written record, overriding any fields you pass."""

    def _make(session_id: str = "", **overrides: Any) -> dict[str, Any]:
        record = dict(BASE_RECORD)
        record.update(overrides)
        if session_id:
            record["sessionId"] = session_id
        path = os.path.join(account_dir, record["sessionId"] + ".json")
        write_json(path, record)
        return record

    return _make


@pytest.fixture
def projects_root(tmp_path) -> str:
    root = tmp_path / "projects"
    root.mkdir()
    return str(root)


@pytest.fixture
def make_transcript(projects_root):
    """Write a transcript and return its path.

    ``lines`` are raw dicts, so a test can express exactly the shape it needs --
    a tool_result, an isMeta line, a resumed session's inherited lines.
    """

    def _make(
        stem: str,
        lines: list[dict[str, Any]],
        project: str = "-repo",
        nested: str | None = None,
    ) -> str:
        directory = os.path.join(projects_root, project)
        if nested:
            directory = os.path.join(directory, nested)
        os.makedirs(directory, exist_ok=True)
        path = os.path.join(directory, stem + ".jsonl")
        with open(path, "w", encoding="utf-8") as f:
            for line in lines:
                f.write(json.dumps(line) + "\n")
        return path

    return _make


def user_line(
    text: str, session_id: str = "s1", ts: str = "2026-01-01T00:00:00Z", **extra: Any
) -> dict[str, Any]:
    line = {
        "type": "user",
        "sessionId": session_id,
        "timestamp": ts,
        "isSidechain": False,
        "cwd": "/repo",
        "message": {"content": text},
    }
    line.update(extra)
    return line


def assistant_line(
    text: str = "ok", session_id: str = "s1", ts: str = "2026-01-01T00:00:01Z"
) -> dict[str, Any]:
    return {
        "type": "assistant",
        "sessionId": session_id,
        "timestamp": ts,
        "isSidechain": False,
        "cwd": "/repo",
        "message": {"content": [{"type": "text", "text": text}]},
    }


def tool_result_line(
    session_id: str = "s1", ts: str = "2026-01-01T00:00:02Z"
) -> dict[str, Any]:
    return {
        "type": "user",
        "sessionId": session_id,
        "timestamp": ts,
        "isSidechain": False,
        "cwd": "/repo",
        "message": {"content": [{"type": "tool_result", "content": "out"}]},
    }
