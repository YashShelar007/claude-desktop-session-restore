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

from claude_desktop_restore.index import find_transcripts, load_manifest, split_records
from claude_desktop_restore.paths import (
    IndexNotFound,
    list_account_dirs,
    resolve_index_root,
)
from claude_desktop_restore.records import build_core
from claude_desktop_restore.transcripts import read_transcript, session_title, to_epoch_ms

pytestmark = pytest.mark.real

PROJECTS = os.path.join(os.path.expanduser("~"), ".claude", "projects")

# Below this many matched sessions, a hit rate is not evidence of anything. The
# repo's own claim standard applies to its tests: don't assert a rate from a
# sample too small to support one.
MIN_SAMPLE_FOR_RATES = 20


def _global_bounds(path: str) -> tuple[str | None, str | None]:
    """First and last timestamp in a transcript, ignoring session scoping.

    Deliberately *not* the session-scoped values ``read_transcript`` returns:
    forging tools take the global ones, so that is what a forged record's
    timestamps will match.
    """
    first = last = None
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            ts = obj.get("timestamp")
            if ts:
                if first is None:
                    first = ts
                last = ts
    return first, last


def forgery_signals(record: dict, transcript: str | None = None) -> list[str]:
    """Names of the signals saying this record was forged, not written by the app.

    The manifest (``.restore-manifest.json``) is the designed answer and is
    checked first, but it is not reliable: a machine can carry records from an
    older version of this tool, or from one of the other tools in this space,
    with no manifest entry at all. On such a machine the real suite would grade
    this tool's derivations against its own stale output -- circular, and
    guaranteed to fail. That is exactly what happened on the Windows machine
    this was written for.

    So there are three structural signals. Each was checked against 67-74 known
    app-written records and fired on **none** of them:

    - ``lastFocusedAt == lastActivityAt``. The app re-stamps ``lastFocusedAt``
      whenever the window regains focus; a forging tool has nothing to re-stamp.
      Weakest of the three, because opening a forged session in the app clears
      it -- which is why the other two exist.
    - ``createdAt`` exactly equal to the transcript's first timestamp. The app
      stamps at session creation, ~1.8 s before the first message, and never
      re-stamps. Across 67 records the delta never came within 50 ms of zero.
    - ``lastActivityAt`` exactly equal to the transcript's last timestamp.

    A session the app created, never re-focused, and stamped with impossible
    precision could in principle look forged. That costs a sample, which is the
    safe direction to be wrong in.
    """
    fired = []

    focused, active = record.get("lastFocusedAt"), record.get("lastActivityAt")
    if focused is not None and focused == active:
        fired.append("lastFocusedAt==lastActivityAt")

    if transcript:
        first, last = _global_bounds(transcript)
        if first is not None and record.get("createdAt") == to_epoch_ms(first):
            fired.append("createdAt==first timestamp")
        if last is not None and active is not None and active == to_epoch_ms(last):
            fired.append("lastActivityAt==last timestamp")

    return fired


@pytest.fixture(scope="module")
def app_records():
    """Records the *app* wrote. Records this tool wrote are excluded.

    This matters more than it looks. On a machine where a previous run of this
    tool forged records, grading a derivation against them is circular -- and if
    those records came from an older version, it grades the new derivation
    against the old version's bugs. ``.restore-manifest.json`` is what
    distinguishes them, and it is the same exclusion the tool itself makes.
    """
    try:
        root = resolve_index_root()
    except IndexNotFound:
        pytest.skip("no Claude Desktop index on this machine")

    records, by_manifest, by_signal = [], 0, 0
    for d in list_account_dirs(root):
        authored = load_manifest(d.path)
        _existing, not_in_manifest = split_records(d.path, authored)
        by_manifest += len(authored)
        for record in not_in_manifest:
            if forgery_signals(record):
                by_signal += 1
            else:
                records.append(record)

    if by_manifest or by_signal:
        print(
            f"\n  excluded {by_manifest + by_signal} forged record(s): "
            f"{by_manifest} by manifest, {by_signal} by lastFocusedAt signal"
        )
    if not records:
        pytest.skip(
            f"no app-written records here ({by_manifest + by_signal} look forged). "
            "Start one Code session in the app, let it finish, and re-run."
        )
    print(f"  {len(records)} app-written record(s) to check against")
    return records


@pytest.fixture(scope="module")
def matched(app_records):
    """(record, parsed transcript) for every record whose transcript exists."""
    if not os.path.isdir(PROJECTS):
        pytest.skip("no ~/.claude/projects on this machine")
    by_stem = {
        os.path.splitext(os.path.basename(p))[0]: p for p in find_transcripts(PROJECTS)
    }
    pairs, forged = [], 0
    for record in app_records:
        path = by_stem.get(record.get("cliSessionId"))
        if not path:
            continue
        # The transcript-dependent forgery signals can only run here, where the
        # transcript is in hand. A record that survived the cheap check in
        # app_records can still fail these.
        signals = forgery_signals(record, path)
        if signals:
            forged += 1
            print(
                f"  forged, excluded: {record.get('title', '')[:44]!r} "
                f"({', '.join(signals)})"
            )
            continue
        parsed = read_transcript(path, record["cliSessionId"])
        if parsed and parsed.first_ts:
            pairs.append((record, parsed))
    if len(pairs) < 5:
        pytest.skip(
            f"only {len(pairs)} app-written record(s) have a transcript on this "
            "machine; too few to mean anything"
        )
    print(f"  {len(pairs)} session(s) matched to their transcript")
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

    Always prints the rates -- that is the useful output, and it is what someone
    reporting a new platform should paste. Only *asserts* them once the sample
    is big enough for a rate to mean anything. This repo asks contributors to
    carry n with every claim; its own tests should hold to that rather than
    turning n=8 into a verdict.
    """
    checks = [
        ("cwd", 0.80, lambda r, p: p.cwd == r.get("cwd")),
        ("originCwd", 0.85, lambda r, p: p.origin_cwd == r.get("originCwd")),
        ("title", 0.80, lambda r, p: session_title(p)[0] == r.get("title")),
        (
            "createdAt (60s)",
            0.95,
            lambda r, p: (
                abs((to_epoch_ms(p.first_ts) or 0) - r.get("createdAt", 0)) <= 60_000
            ),
        ),
    ]

    results = []
    for name, floor, predicate in checks:
        hits, total = _rate(matched, predicate)
        results.append((name, hits, total, floor))
        print(f"  {name:<16} {hits:>4}/{total:<4} ({hits / total:.0%})")

    if len(matched) < MIN_SAMPLE_FOR_RATES:
        pytest.skip(
            f"n={len(matched)} is below {MIN_SAMPLE_FOR_RATES}; "
            "rates reported, not asserted"
        )

    for name, hits, total, floor in results:
        assert hits / total >= floor, f"{name} {hits}/{total}"


def test_completed_turns_is_user_turns_not_assistant_lines(matched):
    """The hypothesis this repo settled. Exact on 42/61 on the reference machine.

    The assistant-line count is not merely less accurate -- it is off by an
    order of magnitude, so a loose bound separates the two decisively.
    """
    with_turns = [(r, p) for r, p in matched if "completedTurns" in r]
    if len(with_turns) < 5:
        pytest.skip("too few records carry completedTurns")

    n = len(with_turns)
    exact = sum(1 for r, p in with_turns if p.turns == r["completedTurns"])
    within_one = sum(1 for r, p in with_turns if abs(p.turns - r["completedTurns"]) <= 1)

    # The assistant-line count is the rival hypothesis. Reported alongside so a
    # failure says which way it failed.
    print(f"  completedTurns   exact {exact}/{n}  within +/-1 {within_one}/{n}")
    for record, parsed in with_turns[:5]:
        print(
            f"    recorded={record['completedTurns']:<5} derived={parsed.turns:<5} "
            f"{record.get('title', '')[:40]}"
        )

    if n < MIN_SAMPLE_FOR_RATES:
        pytest.skip(f"n={n} is below {MIN_SAMPLE_FOR_RATES}; reported, not asserted")

    assert exact / n >= 0.50, f"exact {exact}/{n}"
    assert within_one / n >= 0.65, f"within +/-1 {within_one}/{n}"
