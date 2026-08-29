#!/usr/bin/env python3
"""Enforce the safety rules that CONTRIBUTING.md calls load-bearing.

These are not style checks. Each one corresponds to a way this tool could
destroy something a user cannot get back, or silently produce records the app
will reject. They are cheap enough to run on every commit:

    python3 scripts/check_invariants.py

The test suite covers behaviour; this covers the shape of the source, so that a
refactor cannot quietly drop a guard while keeping the tests green.
"""

from __future__ import annotations

import ast
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src", "claude_desktop_restore")

failures: list[str] = []


def fail(rule: str, detail: str) -> None:
    failures.append(f"{rule}\n    {detail}")


def read(name: str) -> str:
    with open(os.path.join(SRC, name), encoding="utf-8") as f:
        return f.read()


def code_of(source: str, func: str) -> str:
    """A function's body with docstrings stripped.

    Necessary because these rules are *also* described in the docstrings they
    guard -- scanning raw text matches the explanation as well as a violation.
    """
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == func:
            body = node.body
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                body = body[1:]
            return "\n".join(ast.unparse(stmt) for stmt in body)
    return ""


def check_transcripts_are_read_only() -> None:
    """``~/.claude/projects`` is the source of truth and is never written."""
    source = code_of(read("transcripts.py"), "read_transcript")
    banned = [
        (r'open\([^)]*["\'][wa]\+?["\']', "opens a file for writing"),
        (r"\bos\.(remove|unlink|rmdir|rename)\b", "removes or renames a path"),
        (r"\bshutil\.(move|rmtree|copy\w*)\b", "moves or deletes a tree"),
    ]
    for pattern, what in banned:
        if re.search(pattern, source):
            fail("transcripts.py must never write", f"it {what}")


def check_no_bom_on_write() -> None:
    """``utf-8-sig`` emits a BOM on write, and the app's parser then rejects the
    record outright. It is correct for reading only."""
    body = code_of(read("index.py"), "write_json")
    if not body:
        fail("index.py must define write_json", "not found")
        return
    if "utf-8-sig" in body:
        fail("write_json must not use utf-8-sig", "a BOM makes the app reject the record")
    if "'utf-8'" not in body and '"utf-8"' not in body:
        fail("write_json must write explicit UTF-8", "no encoding= found")
    if "separators=" not in body:
        fail("write_json must write minified JSON", "no separators= found")


def check_dry_run_is_the_default() -> None:
    """A tool that writes by default is a tool that writes when someone was
    only looking."""
    sys.path.insert(0, os.path.join(ROOT, "src"))
    from claude_desktop_restore.cli import build_parser

    for action in build_parser()._actions:
        if action.dest == "apply":
            if action.default is not False:
                fail("--apply must default to False", f"default is {action.default!r}")
            return
    fail("--apply must exist", "no --apply flag found")


def check_tombstones_are_honoured() -> None:
    """Every prior tool in this space resurrects deleted sessions because it does
    not look for these. Losing the check would be a silent regression."""
    cli = read("cli.py")
    if "find_tombstones" not in cli:
        fail("the CLI must check tombstones", "find_tombstones is not called")
        return
    if "include_deleted" not in cli:
        fail("tombstones must be skippable only explicitly", "no --include-deleted")


def check_backup_precedes_write() -> None:
    """The index is backed up before the first record is written, not after."""
    cli = read("cli.py")
    if "backup_index" not in cli:
        fail("the CLI must back up before writing", "backup_index is not called")
        return
    if cli.index("backup_index") > cli.index("write_records("):
        fail("backup must precede the write", "backup_index appears after write_records")


def main() -> int:
    for check in (
        check_transcripts_are_read_only,
        check_no_bom_on_write,
        check_dry_run_is_the_default,
        check_tombstones_are_honoured,
        check_backup_precedes_write,
    ):
        check()

    if failures:
        print("Safety invariants violated:\n", file=sys.stderr)
        for f in failures:
            print(f"  - {f}\n", file=sys.stderr)
        print("See CONTRIBUTING.md > Hard rules.", file=sys.stderr)
        return 1
    print("All safety invariants hold.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
