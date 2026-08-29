"""Derivations from a transcript.

Each test here corresponds to a derivation that was measured against real
app-written records; the numbers in the docstrings are from that pass.
"""

from __future__ import annotations

from conftest import assistant_line, tool_result_line, user_line

from claude_desktop_restore.transcripts import (
    Transcript,
    origin_of,
    read_transcript,
    session_title,
    to_epoch_ms,
)


class TestCompletedTurns:
    """It counts human turns, not user lines and not assistant lines.

    Counting every ``user`` line matched 1 of 61 real records, with a median
    21x overcount. The predicate below matched 42 of 61 exactly.
    """

    def test_counts_human_turns_not_assistant_lines(self, make_transcript):
        path = make_transcript(
            "s1",
            [user_line("hello"), assistant_line(), assistant_line(), assistant_line()],
        )
        assert read_transcript(path, "s1").turns == 1

    def test_tool_results_are_not_turns(self, make_transcript):
        path = make_transcript(
            "s1", [user_line("hello"), tool_result_line(), tool_result_line()]
        )
        assert read_transcript(path, "s1").turns == 1

    def test_sidechain_lines_are_not_turns(self, make_transcript):
        path = make_transcript(
            "s1", [user_line("hello"), user_line("sub", isSidechain=True)]
        )
        assert read_transcript(path, "s1").turns == 1

    def test_meta_lines_are_not_turns(self, make_transcript):
        path = make_transcript("s1", [user_line("hello"), user_line("x", isMeta=True)])
        assert read_transcript(path, "s1").turns == 1

    def test_interrupts_are_not_turns(self, make_transcript):
        path = make_transcript(
            "s1",
            [user_line("hello"), user_line("[Request interrupted by user]")],
        )
        assert read_transcript(path, "s1").turns == 1

    def test_slash_command_scaffolding_does_count(self, make_transcript):
        """Excluding it dropped the real-record match rate from 41/61 to 33/61."""
        path = make_transcript(
            "s1", [user_line("hello"), user_line("<command-name>/review</command-name>")]
        )
        assert read_transcript(path, "s1").turns == 2

    def test_resumed_parent_lines_are_excluded(self, make_transcript):
        """A resumed transcript carries the parent's lines, keeping its sessionId.

        Without this filter one real session read 206 turns against a recorded
        67; scoped to its own id it reads 68.
        """
        path = make_transcript(
            "s2",
            [
                user_line("from parent", session_id="s1"),
                user_line("from parent", session_id="s1"),
                user_line("mine", session_id="s2"),
            ],
        )
        assert read_transcript(path, "s2").turns == 1


class TestTimestampsAndCwd:
    def test_scoped_to_own_session(self, make_transcript):
        """One real session's createdAt was 19 days out without this."""
        path = make_transcript(
            "s2",
            [
                user_line("old", session_id="s1", ts="2026-01-01T00:00:00Z"),
                user_line("mine", session_id="s2", ts="2026-01-20T00:00:00Z"),
            ],
        )
        parsed = read_transcript(path, "s2")
        assert parsed.first_ts == "2026-01-20T00:00:00Z"
        assert parsed.last_ts == "2026-01-20T00:00:00Z"

    def test_falls_back_when_no_own_lines_match(self, make_transcript):
        path = make_transcript("s2", [user_line("only", session_id="other")])
        assert read_transcript(path, "s2").first_ts == "2026-01-01T00:00:00Z"

    def test_main_chain_detection(self, make_transcript):
        path = make_transcript("s1", [user_line("hi", isSidechain=True)])
        assert read_transcript(path, "s1").has_main_chain is False


class TestOriginCwd:
    """originCwd is the repo root for worktree sessions, not cwd.

    Deriving it as ``cwd`` scored 23/62 against real records; this rule 59/62.
    """

    def test_worktree_path_yields_repo_root(self):
        assert origin_of("/repo/.claude/worktrees/feature-abc123") == "/repo"

    def test_windows_separators(self):
        assert origin_of(r"C:\repo\.claude\worktrees\feature") == r"C:\repo"

    def test_plain_path_unchanged(self):
        assert origin_of("/repo/src") == "/repo/src"

    def test_nested_repo_path(self):
        assert origin_of("/a/b/frontend/.claude/worktrees/x-1") == "/a/b/frontend"

    def test_none_is_safe(self):
        assert origin_of(None) is None


class TestTitle:
    """titleSource is the app's enum: "user" or "auto". Never "custom"."""

    def test_custom_title_wins_and_is_user_sourced(self):
        t = Transcript(custom_title="My name", ai_title="Generated", first_user_msg="hi")
        assert session_title(t) == ("My name", "user")

    def test_ai_title_is_auto(self):
        t = Transcript(ai_title="Generated", first_user_msg="hi")
        assert session_title(t) == ("Generated", "auto")

    def test_falls_back_to_first_message_truncated(self):
        t = Transcript(first_user_msg="x" * 200)
        title, source = session_title(t)
        assert source == "auto"
        assert len(title) == 60

    def test_whitespace_is_collapsed(self):
        t = Transcript(first_user_msg="a\n\n  b\tc")
        assert session_title(t)[0] == "a b c"

    def test_empty_transcript_gets_placeholder(self):
        assert session_title(Transcript()) == ("Untitled session", "auto")

    def test_scaffolding_is_not_used_as_a_title(self, make_transcript):
        path = make_transcript(
            "s1",
            [user_line("<command-name>/x</command-name>"), user_line("real question")],
        )
        assert session_title(read_transcript(path, "s1"))[0] == "real question"

    def test_caveat_block_is_not_used_as_a_title(self, make_transcript):
        path = make_transcript(
            "s1",
            [
                user_line("Caveat: The messages below were generated while..."),
                user_line("real question"),
            ],
        )
        assert session_title(read_transcript(path, "s1"))[0] == "real question"


class TestEpochMs:
    def test_zulu(self):
        assert to_epoch_ms("2026-01-01T00:00:00Z") == 1767225600000

    def test_offset_is_respected(self):
        assert to_epoch_ms("2026-01-01T00:00:00+00:00") == 1767225600000

    def test_naive_is_treated_as_utc(self):
        assert to_epoch_ms("2026-01-01T00:00:00") == 1767225600000

    def test_none_and_garbage(self):
        assert to_epoch_ms(None) is None
        assert to_epoch_ms("not a date") is None


def test_malformed_lines_are_skipped_not_fatal(make_transcript, projects_root):
    """A recovery tool must not die on one bad line."""
    import os

    path = os.path.join(projects_root, "-repo", "s1.jsonl")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("{not json\n")
        f.write("\n")
        import json as _json

        f.write(_json.dumps(user_line("hello")) + "\n")
    parsed = read_transcript(path, "s1")
    assert parsed.turns == 1
    assert parsed.first_user_msg == "hello"


def test_missing_file_returns_none(tmp_path):
    assert read_transcript(str(tmp_path / "nope.jsonl"), "s1") is None
