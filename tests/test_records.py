"""Building a record from the structural core rather than a cloned reference.

The record's field set is conditional on what the session did, so no single
record is a valid template. These tests pin that.
"""

from __future__ import annotations

import pytest
from conftest import BASE_RECORD

from claude_desktop_restore.records import (
    NEVER_INHERIT,
    NoReferenceRecord,
    build_core,
    build_record,
)
from claude_desktop_restore.transcripts import Transcript


def rec(**overrides):
    r = dict(BASE_RECORD)
    r.update(overrides)
    return r


class TestBuildCore:
    def test_no_records_is_an_error_not_a_guess(self):
        """The tool refuses to invent a schema."""
        with pytest.raises(NoReferenceRecord):
            build_core([])

    def test_single_record_keeps_its_whole_field_set(self):
        """With n=1 the threshold cannot detect conditionality, so it degrades
        to the old clone-a-record behaviour. Never worse, just not better."""
        core = build_core([rec()])
        assert set(core.values) == set(BASE_RECORD)

    def test_conditional_fields_are_dropped(self):
        """A field on one session in ten is not part of the schema."""
        records = [rec(sessionId=f"local_{i}") for i in range(9)]
        records.append(rec(sessionId="local_9", worktreePath="/repo/.claude/worktrees/x"))
        core = build_core(records)
        assert "worktreePath" not in core.values
        assert "worktreePath" in core.dropped

    def test_near_universal_fields_are_kept(self):
        records = [rec(sessionId=f"local_{i}") for i in range(10)]
        for r in records[:-1]:
            r["someNewField"] = True
        core = build_core(records)
        assert core.values["someNewField"] is True

    def test_threshold_is_configurable(self):
        records = [rec(sessionId=f"local_{i}") for i in range(10)]
        records[0]["rare"] = 1
        assert "rare" not in build_core(records).values
        assert "rare" in build_core(records, threshold=0.1).values

    @pytest.mark.parametrize("field", sorted(NEVER_INHERIT))
    def test_denylisted_fields_never_inherit_even_at_n1(self, field):
        """The n=1 backstop. transcriptUnavailable is the dangerous one: inherit
        it and every restored session is marked broken on arrival."""
        core = build_core([rec(**{field: "value"})])
        assert field not in core.values

    def test_values_come_from_the_most_recent_record(self):
        old = rec(sessionId="local_old", lastActivityAt=1, model="claude-old")
        new = rec(sessionId="local_new", lastActivityAt=999, model="claude-new")
        core = build_core([old, new])
        assert core.values["model"] == "claude-new"

    def test_unknown_future_fields_are_carried_through(self):
        """The whole point: an app update that adds a field should survive."""
        records = [rec(sessionId=f"local_{i}", futureThing={"a": 1}) for i in range(3)]
        core = build_core(records)
        assert core.values["futureThing"] == {"a": 1}
        assert "futureThing" in core.inherited


class TestBuildRecord:
    def _core(self):
        return build_core([rec()])

    def _transcript(self, **kw):
        base = dict(
            first_ts="2026-01-01T00:00:00Z",
            last_ts="2026-01-02T00:00:00Z",
            cwd="/repo",
            turns=7,
            has_main_chain=True,
        )
        base.update(kw)
        return Transcript(**base)

    def test_derived_fields_override_the_core(self):
        r = build_record(self._core(), "cli-123", self._transcript())
        assert r["cliSessionId"] == "cli-123"
        assert r["cwd"] == "/repo"
        assert r["completedTurns"] == 7
        assert r["createdAt"] == 1767225600000

    def test_session_id_is_fresh_and_prefixed(self):
        core = self._core()
        a = build_record(core, "c1", self._transcript())["sessionId"]
        b = build_record(core, "c2", self._transcript())["sessionId"]
        assert a.startswith("local_") and a != b

    def test_worktree_origin_cwd(self):
        t = self._transcript(cwd="/repo/.claude/worktrees/feature-1")
        r = build_record(self._core(), "c1", t)
        assert r["cwd"] == "/repo/.claude/worktrees/feature-1"
        assert r["originCwd"] == "/repo"

    def test_per_session_state_is_reset_not_inherited(self):
        """Inheriting remoteMcpServersConfig would copy a 126 KB blob -- and one
        session's MCP grants -- onto every restored session."""
        core = build_core([rec(remoteMcpServersConfig=[{"uuid": "x", "name": "Canva"}])])
        r = build_record(core, "c1", self._transcript())
        assert r["remoteMcpServersConfig"] == []
        assert r["isArchived"] is False

    def test_reset_fields_absent_from_core_are_not_added(self):
        """We never write a field the app didn't write on this machine."""
        lean = {k: v for k, v in BASE_RECORD.items() if k != "spawnSeed"}
        r = build_record(build_core([lean]), "c1", self._transcript())
        assert "spawnSeed" not in r

    def test_field_order_follows_the_app(self):
        core = self._core()
        r = build_record(core, "c1", self._transcript())
        assert list(r) == core.order

    def test_title_from_custom_title_is_user_sourced(self):
        t = self._transcript(custom_title="My session")
        r = build_record(self._core(), "c1", t)
        assert (r["title"], r["titleSource"]) == ("My session", "user")
