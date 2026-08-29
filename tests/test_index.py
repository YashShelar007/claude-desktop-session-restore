"""Disk behaviour: encoding, tombstones, discovery, backup.

Each test corresponds to a rule in CONTRIBUTING.md's "hard rules" list.
"""

from __future__ import annotations

import json
import os

from claude_desktop_restore.index import (
    backup_index,
    find_tombstones,
    find_transcripts,
    load_manifest,
    save_manifest,
    split_records,
    write_json,
    write_records,
)


class TestEncoding:
    """A UTF-8 BOM makes the app's parser reject the record outright."""

    def test_never_writes_a_bom(self, tmp_path):
        path = str(tmp_path / "r.json")
        write_json(path, {"title": "hello"})
        assert not open(path, "rb").read().startswith(b"\xef\xbb\xbf")

    def test_no_trailing_newline(self, tmp_path):
        path = str(tmp_path / "r.json")
        write_json(path, {"a": 1})
        assert not open(path, "rb").read().endswith(b"\n")

    def test_minified(self, tmp_path):
        path = str(tmp_path / "r.json")
        write_json(path, {"a": 1, "b": [1, 2]})
        raw = open(path, "rb").read()
        assert b"\n" not in raw
        assert b", " not in raw and b": " not in raw

    def test_non_ascii_is_raw_utf8_not_escaped(self, tmp_path):
        """The app stores non-ASCII titles as UTF-8, not \\uXXXX escapes."""
        path = str(tmp_path / "r.json")
        write_json(path, {"title": "café — 日本語"})
        raw = open(path, "rb").read()
        assert "café — 日本語".encode() in raw
        assert b"\\u" not in raw

    def test_roundtrips(self, tmp_path):
        path = str(tmp_path / "r.json")
        payload = {"title": "café", "n": 1, "list": [1, {"k": True}]}
        write_json(path, payload)
        assert json.load(open(path, encoding="utf-8")) == payload


class TestTombstones:
    """Deleting in the UI writes deleted_<sessionId> + deleted_<cliSessionId>.

    None of the four prior tools in this space check for these, so all of them
    resurrect every deleted session on a rerun.
    """

    def test_finds_uuids_stripping_the_prefix(self, account_dir):
        for name in ("deleted_aaa", "deleted_bbb"):
            open(os.path.join(account_dir, name), "w").write("1700000000000")
        assert find_tombstones(account_dir) == {"aaa", "bbb"}

    def test_empty_when_none(self, account_dir):
        assert find_tombstones(account_dir) == set()

    def test_ignores_records_and_other_files(self, account_dir):
        open(os.path.join(account_dir, "local_x.json"), "w").write("{}")
        open(os.path.join(account_dir, "scheduled-tasks.json"), "w").write("{}")
        assert find_tombstones(account_dir) == set()


class TestTranscriptDiscovery:
    """Only <project>/<uuid>.jsonl. Anything deeper floods the picker -- on two
    real machines that was 60 sessions among 233 files, and 113 among 329."""

    def test_takes_top_level_only(self, projects_root, make_transcript):
        make_transcript("session-a", [])
        make_transcript("agent-1", [], nested="session-a/subagents")
        make_transcript("journal", [], nested="session-a/subagents/workflows/w1")
        found = [os.path.basename(p) for p in find_transcripts(projects_root)]
        assert found == ["session-a.jsonl"]

    def test_multiple_projects(self, projects_root, make_transcript):
        make_transcript("a", [], project="-repo-one")
        make_transcript("b", [], project="-repo-two")
        assert len(find_transcripts(projects_root)) == 2


class TestRecordsAndManifest:
    def test_split_separates_tool_authored_records(self, account_dir, make_record):
        make_record(session_id="local_app", cliSessionId="cli-app")
        make_record(session_id="local_tool", cliSessionId="cli-tool")
        existing, app_written = split_records(account_dir, {"local_tool"})
        assert set(existing) == {"cli-app", "cli-tool"}
        assert [r["sessionId"] for r in app_written] == ["local_app"]

    def test_unparseable_records_are_skipped(self, account_dir, make_record):
        make_record(session_id="local_good", cliSessionId="cli-good")
        open(os.path.join(account_dir, "local_bad.json"), "w").write("{not json")
        existing, app_written = split_records(account_dir, set())
        assert set(existing) == {"cli-good"}
        assert len(app_written) == 1

    def test_bom_prefixed_record_is_still_readable(self, account_dir):
        """We never write a BOM, but we should survive reading one."""
        path = os.path.join(account_dir, "local_bom.json")
        with open(path, "wb") as f:
            f.write(
                b"\xef\xbb\xbf"
                + json.dumps(
                    {"sessionId": "local_bom", "cliSessionId": "cli-bom"}
                ).encode()
            )
        existing, _ = split_records(account_dir, set())
        assert "cli-bom" in existing

    def test_manifest_roundtrip(self, account_dir):
        assert load_manifest(account_dir) == set()
        save_manifest(account_dir, {"local_a", "local_b"})
        assert load_manifest(account_dir) == {"local_a", "local_b"}

    def test_corrupt_manifest_is_not_fatal(self, account_dir):
        from claude_desktop_restore.index import MANIFEST

        open(os.path.join(account_dir, MANIFEST), "w").write("{not json")
        assert load_manifest(account_dir) == set()

    def test_write_records_names_files_after_session_id(self, account_dir):
        written = write_records(account_dir, [{"sessionId": "local_zzz", "a": 1}])
        assert os.path.basename(written[0]) == "local_zzz.json"


def test_backup_copies_the_whole_index(index_root, account_dir, make_record):
    make_record(session_id="local_x")
    open(os.path.join(account_dir, "deleted_y"), "w").write("1700000000000")
    backup = backup_index(index_root)
    assert os.path.isdir(backup)
    original = sorted(
        os.path.relpath(os.path.join(dp, f), index_root)
        for dp, _, fs in os.walk(index_root)
        for f in fs
    )
    copied = sorted(
        os.path.relpath(os.path.join(dp, f), backup)
        for dp, _, fs in os.walk(backup)
        for f in fs
    )
    assert original == copied
