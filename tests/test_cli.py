"""End-to-end runs against a synthetic index.

The safety rules -- dry-run default, transcripts read-only, tombstones honoured,
backup before write -- are pinned here rather than merely documented.
"""

from __future__ import annotations

import glob
import hashlib
import json
import os

import pytest
from conftest import assistant_line, user_line

from claude_desktop_restore.cli import main, pick_account_dir, report_account_mismatch
from claude_desktop_restore.paths import AccountDir, AccountSignals

ACCOUNT = "11111111-1111-4111-8111-111111111111"


def run(index_root, projects_root, *extra):
    return main(
        [
            "--index-dir",
            index_root,
            "--projects-root",
            projects_root,
            "--min-idle-minutes",
            "0",
            *extra,
        ]
    )


def records_in(account_dir):
    return glob.glob(os.path.join(account_dir, "local_*.json"))


@pytest.fixture
def ready(index_root, account_dir, projects_root, make_record, make_transcript):
    """One app-written record to model on, one unindexed transcript."""
    make_record(session_id="local_app", cliSessionId="cli-app")
    make_transcript("cli-new", [user_line("hello"), assistant_line()])
    return index_root, account_dir, projects_root


class TestDryRunIsTheDefault:
    def test_writes_nothing_without_apply(self, ready, capsys):
        index_root, account_dir, projects_root = ready
        before = set(records_in(account_dir))
        assert run(index_root, projects_root) == 0
        assert set(records_in(account_dir)) == before
        assert "DRY RUN" in capsys.readouterr().out

    def test_creates_no_backup_on_a_dry_run(self, ready):
        index_root, _, projects_root = ready
        run(index_root, projects_root)
        parent = os.path.dirname(index_root)
        assert not glob.glob(os.path.join(parent, "*_backup_*"))

    def test_writes_no_manifest_on_a_dry_run(self, ready):
        index_root, account_dir, projects_root = ready
        run(index_root, projects_root)
        assert not os.path.exists(os.path.join(account_dir, ".restore-manifest.json"))


class TestApply:
    def test_writes_one_record_per_unindexed_transcript(self, ready):
        index_root, account_dir, projects_root = ready
        assert run(index_root, projects_root, "--apply") == 0
        written = [json.load(open(p, encoding="utf-8")) for p in records_in(account_dir)]
        assert {r["cliSessionId"] for r in written} == {"cli-app", "cli-new"}

    def test_backs_up_before_writing(self, ready):
        index_root, _, projects_root = ready
        run(index_root, projects_root, "--apply")
        backups = glob.glob(os.path.join(os.path.dirname(index_root), "*_backup_*"))
        assert len(backups) == 1

    def test_no_backup_flag_is_respected(self, ready):
        index_root, _, projects_root = ready
        run(index_root, projects_root, "--apply", "--no-backup")
        assert not glob.glob(os.path.join(os.path.dirname(index_root), "*_backup_*"))

    def test_second_run_is_a_no_op(self, ready):
        index_root, account_dir, projects_root = ready
        run(index_root, projects_root, "--apply", "--no-backup")
        after_first = set(records_in(account_dir))
        run(index_root, projects_root, "--apply", "--no-backup")
        assert set(records_in(account_dir)) == after_first

    def test_written_record_has_no_bom(self, ready):
        index_root, account_dir, projects_root = ready
        run(index_root, projects_root, "--apply", "--no-backup")
        for path in records_in(account_dir):
            assert not open(path, "rb").read().startswith(b"\xef\xbb\xbf")

    def test_manifest_records_what_the_tool_authored(self, ready):
        index_root, account_dir, projects_root = ready
        run(index_root, projects_root, "--apply", "--no-backup")
        manifest = json.load(
            open(os.path.join(account_dir, ".restore-manifest.json"), encoding="utf-8")
        )
        assert len(manifest) == 1
        assert "local_app" not in manifest


class TestTombstones:
    """The failure mode every other tool in this space has."""

    def test_deleted_sessions_are_not_resurrected(self, ready, account_dir):
        index_root, account_dir, projects_root = ready
        open(os.path.join(account_dir, "deleted_cli-new"), "w").write("1700000000000")
        run(index_root, projects_root, "--apply", "--no-backup")
        written = [json.load(open(p, encoding="utf-8")) for p in records_in(account_dir)]
        assert {r["cliSessionId"] for r in written} == {"cli-app"}

    def test_include_deleted_overrides(self, ready, account_dir):
        index_root, account_dir, projects_root = ready
        open(os.path.join(account_dir, "deleted_cli-new"), "w").write("1700000000000")
        run(index_root, projects_root, "--apply", "--no-backup", "--include-deleted")
        written = [json.load(open(p, encoding="utf-8")) for p in records_in(account_dir)]
        assert {r["cliSessionId"] for r in written} == {"cli-app", "cli-new"}


class TestTranscriptsAreReadOnly:
    """The one rule that matters most: transcripts are the source of truth."""

    def test_apply_does_not_modify_any_transcript(self, ready):
        index_root, _, projects_root = ready

        def fingerprint():
            out = {}
            for dp, _, fs in os.walk(projects_root):
                for f in fs:
                    p = os.path.join(dp, f)
                    out[p] = hashlib.sha256(open(p, "rb").read()).hexdigest()
            return out

        before = fingerprint()
        run(index_root, projects_root, "--apply", "--no-backup")
        assert fingerprint() == before

    def test_apply_creates_no_files_under_projects(self, ready):
        index_root, _, projects_root = ready
        before = {p for dp, _, fs in os.walk(projects_root) for p in fs}
        run(index_root, projects_root, "--apply", "--no-backup")
        after = {p for dp, _, fs in os.walk(projects_root) for p in fs}
        assert after == before


class TestFiltering:
    def test_subagent_transcripts_are_skipped(self, ready, make_transcript):
        index_root, account_dir, projects_root = ready
        make_transcript("agent-xyz", [user_line("sub")])
        run(index_root, projects_root, "--apply", "--no-backup")
        written = [json.load(open(p, encoding="utf-8")) for p in records_in(account_dir)]
        assert "agent-xyz" not in {r["cliSessionId"] for r in written}

    def test_sidechain_only_transcripts_are_skipped(self, ready, make_transcript):
        index_root, account_dir, projects_root = ready
        make_transcript("side-only", [user_line("x", isSidechain=True)])
        run(index_root, projects_root, "--apply", "--no-backup")
        written = [json.load(open(p, encoding="utf-8")) for p in records_in(account_dir)]
        assert "side-only" not in {r["cliSessionId"] for r in written}

    def test_cwd_prefix_filters(self, ready, make_transcript):
        index_root, account_dir, projects_root = ready
        run(
            index_root,
            projects_root,
            "--apply",
            "--no-backup",
            "--cwd-prefix",
            "/somewhere-else",
        )
        written = [json.load(open(p, encoding="utf-8")) for p in records_in(account_dir)]
        assert {r["cliSessionId"] for r in written} == {"cli-app"}

    def test_limit_caps_the_write(
        self, index_root, account_dir, projects_root, make_record, make_transcript
    ):
        make_record(session_id="local_app", cliSessionId="cli-app")
        for i in range(4):
            make_transcript(
                f"cli-{i}", [user_line("hi", ts=f"2026-01-0{i + 1}T00:00:00Z")]
            )
        run(index_root, projects_root, "--apply", "--no-backup", "--limit", "2")
        assert len(records_in(account_dir)) == 3  # 1 pre-existing + 2 written


class TestNoReferenceRecord:
    def test_refuses_rather_than_inventing_a_schema(
        self, index_root, projects_root, make_transcript, capsys
    ):
        make_transcript("cli-new", [user_line("hello")])
        assert run(index_root, projects_root, "--apply") == 2
        assert "refuses to invent a schema" in capsys.readouterr().err


class TestAccountSelection:
    def _dirs(self):
        return [
            AccountDir(path="/i/a/o", account="a", org="o", records=5),
            AccountDir(path="/i/b/o", account="b", org="o", records=50),
        ]

    def test_defaults_to_most_records(self):
        assert pick_account_dir(self._dirs()).account == "b"

    def test_account_flag_overrides(self):
        assert pick_account_dir(self._dirs(), "a").account == "a"

    def test_unknown_account_is_an_error(self):
        with pytest.raises(SystemExit):
            pick_account_dir(self._dirs(), "nope")

    def test_no_folders_is_an_error(self):
        with pytest.raises(SystemExit):
            pick_account_dir([])


class TestAccountMismatchWarnings:
    """Two symptoms that look like bugs but are account scoping."""

    def test_warns_when_the_app_shows_another_account(self, capsys):
        report_account_mismatch(ACCOUNT, AccountSignals(app_account="other"))
        out = capsys.readouterr().out
        assert "signed in as a DIFFERENT account" in out
        assert "will not appear in the" in out

    def test_warns_when_another_account_authored_the_transcripts(self, capsys):
        report_account_mismatch(ACCOUNT, AccountSignals(cli_account="other"))
        out = capsys.readouterr().out
        assert "authored by a DIFFERENT account" in out
        assert "artifacts" in out.lower()

    def test_silent_when_everything_agrees(self, capsys):
        report_account_mismatch(
            ACCOUNT, AccountSignals(cli_account=ACCOUNT, app_account=ACCOUNT)
        )
        assert capsys.readouterr().out == ""

    def test_silent_when_signals_are_unavailable(self, capsys):
        report_account_mismatch(ACCOUNT, AccountSignals())
        assert capsys.readouterr().out == ""
