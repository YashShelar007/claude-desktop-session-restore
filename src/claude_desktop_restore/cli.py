"""Command-line entry point."""

from __future__ import annotations

import argparse
import os
import sys
import time
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from . import __version__
from .index import (
    backup_index,
    find_tombstones,
    find_transcripts,
    load_manifest,
    save_manifest,
    split_records,
    write_records,
)
from .paths import (
    AccountDir,
    AccountSignals,
    IndexNotFound,
    list_account_dirs,
    read_account_signals,
    resolve_index_root,
)
from .records import (
    DEFAULT_CORE_THRESHOLD,
    NoReferenceRecord,
    build_core,
    build_record,
)
from .transcripts import read_transcript


def step(msg: str) -> None:
    print(f"==> {msg}")


def ok(msg: str) -> None:
    print(f"  + {msg}")


def warn(msg: str) -> None:
    print(f"  ! {msg}")


def pick_account_dir(
    dirs: Sequence[AccountDir], want_account: str | None = None
) -> AccountDir:
    if not dirs:
        raise SystemExit(
            "No <accountUuid>/<orgUuid> folder pair found.\n"
            "Start a session in the app first."
        )
    if want_account:
        matches = [d for d in dirs if d.account == want_account]
        if not matches:
            found = ", ".join(sorted({d.account for d in dirs}))
            raise SystemExit(f"No folder for account {want_account}.\nFound: {found}")
        return max(matches, key=lambda d: d.records)

    ranked = sorted(dirs, key=lambda d: -d.records)
    if len(ranked) > 1:
        warn("Multiple account/org folders found; using the one with the most records:")
        for d in ranked:
            print(f"      {d.account}/{d.org}  ({d.records} records)")
    return ranked[0]


def report_account_mismatch(account: str, signals: AccountSignals) -> None:
    """Two failure modes that look like bugs but are account scoping.

    Neither is recoverable after the fact by staring at the picker, so they are
    reported before anything is written.
    """
    if signals.app_account and signals.app_account != account:
        warn("The Desktop app is signed in as a DIFFERENT account:")
        print(f"      writing into : {account}")
        print(f"      app shows    : {signals.app_account}")
        print("      Records written here are correct but will not appear in the")
        print("      picker until you sign in as the first account. Use --account")
        print("      to target the signed-in one instead.")
    if signals.cli_account and signals.cli_account != account:
        warn("The transcripts were authored by a DIFFERENT account:")
        print(f"      writing into : {account}")
        print(f"      authored by  : {signals.cli_account}")
        print("      Conversation history will restore in full -- it is read from")
        print("      the local transcript. Artifacts published in these sessions")
        print("      are server-side and account-scoped, so they will show as")
        print("      unavailable. Nothing on disk can change that.")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="restore-desktop-sessions",
        description=(
            "Rebuild the Claude Desktop Code session picker from CLI transcripts. "
            "Writes nothing without --apply."
        ),
    )
    p.add_argument("--version", action="version", version="%(prog)s " + __version__)
    p.add_argument(
        "--apply",
        action="store_true",
        help="Write records. Without it, nothing is written.",
    )
    p.add_argument(
        "--cwd-prefix",
        metavar="P",
        help="Only transcripts whose recorded cwd starts with P.",
    )
    p.add_argument(
        "--limit",
        type=int,
        default=0,
        metavar="N",
        help="Only the N most recently active.",
    )
    p.add_argument("--index-dir", help="Override index auto-detection.")
    p.add_argument(
        "--account",
        metavar="UUID",
        help="Write into this account's folder instead of the one with the most records.",
    )
    p.add_argument("--projects-root", help="Override ~/.claude/projects.")
    p.add_argument(
        "--include-deleted",
        action="store_true",
        help="Re-index tombstoned sessions. Off by default.",
    )
    p.add_argument(
        "--min-idle-minutes",
        type=int,
        default=2,
        metavar="N",
        help="Skip transcripts touched in the last N minutes (default 2).",
    )
    p.add_argument(
        "--core-threshold",
        type=float,
        default=DEFAULT_CORE_THRESHOLD,
        metavar="F",
        help="Keep fields present in >= this fraction of app-written "
        f"records (default {DEFAULT_CORE_THRESHOLD:.1f}).",
    )
    p.add_argument(
        "--no-backup",
        action="store_true",
        help="Skip the pre-write backup. Not recommended.",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    step("Locating the Claude Desktop session index")
    try:
        index_root = resolve_index_root(args.index_dir)
    except IndexNotFound as exc:
        print(str(exc), file=sys.stderr)
        return 2

    acct = pick_account_dir(list_account_dirs(index_root), args.account)
    ok(f"index:   {acct.path}")
    ok(f"account={acct.account}  org={acct.org}")
    report_account_mismatch(acct.account, read_account_signals(index_root))

    step("Modelling the schema on records the app wrote here")
    authored = load_manifest(acct.path)
    existing, app_written = split_records(acct.path, authored)
    try:
        core = build_core(app_written, args.core_threshold)
    except NoReferenceRecord as exc:
        print(str(exc), file=sys.stderr)
        return 2

    ok(
        f"{core.sample} app-written record(s); "
        f"{len(core.presence)} distinct field(s) seen"
    )
    ok(
        f"structural core: {len(core.values)} field(s) present in "
        f">={args.core_threshold * 100:.0f}% of them"
    )
    if core.dropped:
        dropped = ", ".join(core.dropped)
        print(f"      conditional/per-session, not inherited: {dropped}")
    if core.inherited:
        print(f"      inheriting verbatim: {', '.join(core.inherited)}")

    tombstoned = find_tombstones(acct.path)
    if tombstoned:
        ok(
            f"{len(tombstoned)} tombstone file(s) found; "
            "deleted sessions will be left alone"
        )

    step("Scanning transcripts")
    projects_root = args.projects_root or os.path.join(
        os.path.expanduser("~"), ".claude", "projects"
    )
    if not os.path.isdir(projects_root):
        print(f"Transcript root not found: {projects_root}", file=sys.stderr)
        return 2

    records: list[dict[str, Any]] = []
    skips: dict[str, int] = {}

    def skip(reason: str) -> None:
        skips[reason] = skips.get(reason, 0) + 1

    cutoff = time.time() - args.min_idle_minutes * 60
    for path in find_transcripts(projects_root):
        base = os.path.basename(path)
        stem = os.path.splitext(base)[0]

        if base.startswith("agent-"):
            skip("subagent transcript (agent-*)")
            continue
        if stem in existing:
            skip("already indexed")
            continue
        if not args.include_deleted and stem in tombstoned:
            skip("deleted in the app (tombstoned)")
            continue
        if args.min_idle_minutes > 0 and os.path.getmtime(path) > cutoff:
            skip(f"still active (modified < {args.min_idle_minutes} min ago)")
            continue

        parsed = read_transcript(path, stem)
        if parsed is None:
            skip("unreadable")
            continue
        if not parsed.has_main_chain:
            skip("no main chain (sidechain-only)")
            continue
        if not parsed.cwd:
            skip("no cwd recorded")
            continue
        if not parsed.first_ts:
            skip("no timestamps")
            continue
        if args.cwd_prefix and not parsed.cwd.startswith(args.cwd_prefix):
            skip("cwd outside --cwd-prefix")
            continue

        records.append(build_record(core, stem, parsed))

    records.sort(key=lambda r: r.get("lastActivityAt") or 0, reverse=True)
    if args.limit > 0:
        records = records[: args.limit]

    print()
    print(f"{'#':<3} {'LAST ACTIVE':<17} {'TITLE':<46} CWD")
    for i, record in enumerate(records, 1):
        when = datetime.fromtimestamp(record["lastActivityAt"] / 1000)
        stamp = when.strftime("%Y-%m-%d %H:%M")
        print(f"{i:<3} {stamp:<17} {record['title'][:44]:<46} {record['cwd']}")
    print()

    if skips:
        step("Skipped")
        for reason, count in sorted(skips.items(), key=lambda kv: -kv[1]):
            print(f"  {count:5d}  {reason}")
        print()

    if not args.apply:
        warn(
            f"DRY RUN -- {len(records)} record(s) would be written. Re-run with --apply."
        )
        return 0
    if not records:
        ok("Nothing to do.")
        return 0

    if not args.no_backup:
        step("Backing up the index")
        backup = backup_index(index_root)
        ok(f"backup: {backup}")
        print(f"      restore: rm -rf '{index_root}' && cp -R '{backup}' '{index_root}'")

    step(f"Writing {len(records)} record(s)")
    write_records(acct.path, records)
    save_manifest(acct.path, authored | {r["sessionId"] for r in records})
    ok("Done. Restart Claude Desktop and open the Code session picker.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
