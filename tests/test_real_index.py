"""Validation against this machine's own Claude state.

Marked ``real`` and excluded by default: these read ``~/.claude`` and the
Claude Desktop index, so they only mean anything on a machine that has both.
Run with ``pytest -m real``.

This is the harness behind the README's validation table. Every derivation is
compared against the record the app itself wrote for the *same* session, which
is the only check that has ever caught a real bug here -- four of them, all
invisible against a single hand-picked record.

Nothing here writes anything.
"""

from __future__ import annotations

import glob
import json
import os

import pytest

from claude_desktop_restore.index import find_transcripts
from claude_desktop_restore.paths import (
    IndexNotFound,
    list_account_dirs,
    resolve_index_root,
)
from claude_desktop_restore.records import build_core
from claude_desktop_restore.transcripts import read_transcript, session_title, to_epoch_ms

pytestmark = pytest.mark.real

PROJECTS = os.path.join(os.path.expanduser("~"), ".claude", "projects")


@pytest.fixture(scope="module")
def app_records():
    try:
        root = resolve_index_root()
    except IndexNotFound:
        pytest.skip("no Claude Desktop index on this machine")
    records = []
    for d in list_account_dirs(root):
        for path in glob.glob(os.path.join(d.path, "local_*.json")):
            try:
                with open(path, encoding="utf-8-sig") as f:
                    records.append(json.load(f))
            except Exception:
                continue
    if not records:
        pytest.skip("no app-written records on this machine")
    return records


@pytest.fixture(scope="module")
def matched(app_records):
    """(record, parsed transcript) for every record whose transcript exists."""
    if not os.path.isdir(PROJECTS):
        pytest.skip("no ~/.claude/projects on this machine")
    by_stem = {
        os.path.splitext(os.path.basename(p))[0]: p for p in find_transcripts(PROJECTS)
    }
    pairs = []
    for record in app_records:
        path = by_stem.get(record.get("cliSessionId"))
        if not path:
            continue
        parsed = read_transcript(path, record["cliSessionId"])
        if parsed and parsed.first_ts:
            pairs.append((record, parsed))
    if len(pairs) < 5:
        pytest.skip("too few matched sessions to be meaningful")
    return pairs


def _rate(pairs, predicate):
    hits = sum(1 for r, p in pairs if predicate(r, p))
    return hits, len(pairs)


def test_records_carry_no_bom(app_records):
    """Not one of the app's own records has a BOM, which is why ours must not."""
    root = resolve_index_root()
    for path in glob.glob(os.path.join(root, "*", "*", "local_*.json")):
        assert not open(path, "rb").read().startswith(b"\xef\xbb\xbf"), path


def test_records_are_minified_without_trailing_newline(app_records):
    root = resolve_index_root()
    for path in glob.glob(os.path.join(root, "*", "*", "local_*.json")):
        raw = open(path, "rb").read()
        assert b"\n" not in raw, path


def test_title_source_enum_is_user_or_auto(app_records):
    """ "custom" is a value the app never writes."""
    seen = {r.get("titleSource") for r in app_records if "titleSource" in r}
    assert seen <= {"user", "auto"}, seen


def test_title_source_user_always_matches_custom_title(matched):
    """24/24 on the reference machine, with no exceptions."""
    checked = 0
    for record, parsed in matched:
        if record.get("titleSource") != "user":
            continue
        checked += 1
        assert parsed.custom_title == record["title"]
    if checked == 0:
        pytest.skip("no user-titled sessions on this machine")


def test_enabled_mcp_tools_implies_remote_mcp_servers(app_records):
    """The correlation that showed the field was conditional, not dropped."""
    for record in app_records:
        if "enabledMcpTools" in record:
            assert record.get("remoteMcpServersConfig"), record.get("sessionId")


def test_tombstones_come_in_pairs():
    """78 tombstones resolved into exactly 39 pairs on the reference machine."""
    root = resolve_index_root()
    for d in list_account_dirs(root):
        by_timestamp = {}
        for path in glob.glob(os.path.join(d.path, "deleted_*")):
            value = open(path, encoding="utf-8", errors="replace").read().strip()
            by_timestamp.setdefault(value, []).append(path)
        for value, group in by_timestamp.items():
            assert value.isdigit(), value
            assert len(group) == 2, (value, group)


def test_structural_core_excludes_conditional_fields(app_records):
    if len(app_records) < 10:
        pytest.skip("core threshold is only meaningful with several records")
    core = build_core(app_records)
    conditional = ("prNumber", "worktreePath", "transcriptUnavailable", "enabledMcpTools")
    for field in conditional:
        assert field not in core.values


def test_derivation_hit_rates(matched):
    """Reproduces the README's validation table.

    Thresholds are set below the measured rates, not at them -- this is a
    regression guard, not a claim that the numbers are laws.
    """
    hits, total = _rate(matched, lambda r, p: p.cwd == r.get("cwd"))
    assert hits / total >= 0.80, f"cwd {hits}/{total}"

    hits, total = _rate(matched, lambda r, p: p.origin_cwd == r.get("originCwd"))
    assert hits / total >= 0.85, f"originCwd {hits}/{total}"

    hits, total = _rate(matched, lambda r, p: session_title(p)[0] == r.get("title"))
    assert hits / total >= 0.80, f"title {hits}/{total}"

    hits, total = _rate(
        matched,
        lambda r, p: (
            abs((to_epoch_ms(p.first_ts) or 0) - r.get("createdAt", 0)) <= 60_000
        ),
    )
    assert hits / total >= 0.95, f"createdAt {hits}/{total}"


def test_completed_turns_is_user_turns_not_assistant_lines(matched):
    """The hypothesis this repo settled. Exact on 42/61 on the reference machine.

    The assistant-line count is not merely less accurate -- it is off by an
    order of magnitude, so a loose bound separates the two decisively.
    """
    with_turns = [(r, p) for r, p in matched if "completedTurns" in r]
    if len(with_turns) < 5:
        pytest.skip("too few records carry completedTurns")

    exact = sum(1 for r, p in with_turns if p.turns == r["completedTurns"])
    within_one = sum(1 for r, p in with_turns if abs(p.turns - r["completedTurns"]) <= 1)
    assert exact / len(with_turns) >= 0.50, f"exact {exact}/{len(with_turns)}"
    assert within_one / len(with_turns) >= 0.65, (
        f"within +/-1 {within_one}/{len(with_turns)}"
    )
