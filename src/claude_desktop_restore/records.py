"""Building an index record without inventing a schema.

The record's field set is **conditional on what the session did** -- worktree
fields only for worktree sessions, ``pr*`` only where a PR was opened,
``enabledMcpTools`` only where remote MCP servers were configured. 69 observed
records carry 44 distinct fields in 44 distinct field-set signatures.

So there is no correct fixed field list, and cloning a single live record is
worse than hardcoding one: the richest record on the machine this was validated
against would stamp every restored session with its PR number, its worktree path
and a stale prompt suggestion. See SCHEMA.md.
"""

from __future__ import annotations

import uuid as uuid_mod
from collections.abc import Sequence
from typing import Any

from .transcripts import Transcript, session_title, to_epoch_ms

# Derived per session; never inherited.
DERIVED_FIELDS = frozenset(
    {
        "sessionId",
        "cliSessionId",
        "cwd",
        "originCwd",
        "createdAt",
        "lastActivityAt",
        "lastFocusedAt",
        "title",
        "titleSource",
        "completedTurns",
    }
)

# Per-session runtime state. Reset to an empty value, but only if the core
# actually carries the field -- we never add a field the app didn't write.
RESET_FIELDS: dict[str, Any] = {
    "remoteMcpServersConfig": [],
    "alwaysAllowedReasons": [],
    "sessionPermissionUpdates": [],
    "spawnSeed": {},
    "isArchived": False,
}

# Fields that are dangerous rather than merely wrong to inherit. The presence
# threshold already excludes these on a machine with many app-written records;
# this is the backstop for a machine that has only one, where conditionality is
# invisible. transcriptUnavailable is the worst of them -- inherit it and every
# restored session is marked broken on arrival.
NEVER_INHERIT = frozenset(
    {
        "transcriptUnavailable",
        "error",
        "errorAt",
        "forkedFromSessionId",
        "spawnedFrom",
        "dispatchParentOrigin",
        "prNumber",
        "prUrl",
        "prRepository",
        "prState",
        "prs",
        "branch",
        "sourceBranch",
        "writtenBranches",
        "worktreeName",
        "worktreePath",
        "promptSuggestion",
        "chromeTabGroupId",
        "color",
        "enabledMcpTools",
    }
)

DEFAULT_CORE_THRESHOLD = 0.9


class NoReferenceRecord(Exception):
    """No app-written record to model the schema on."""

    def __init__(self) -> None:
        super().__init__(
            "No app-written record to model the schema on.\n\n"
            "Open Claude Desktop, start a Code session in a real folder, send one\n"
            "message, let it finish, quit, then re-run. This tool deliberately\n"
            "refuses to invent a schema: the format is undocumented and the field\n"
            "set varies per session."
        )


class Core:
    """The structural core: fields the app writes for nearly every session."""

    def __init__(
        self,
        values: dict[str, Any],
        order: list[str],
        presence: dict[str, int],
        sample: int,
    ):
        self.values = values
        self.order = order
        self.presence = presence
        self.sample = sample

    @property
    def dropped(self) -> list[str]:
        """Fields seen in the corpus but excluded as conditional or unsafe."""
        return sorted(
            f for f in self.presence if f not in self.values and f not in DERIVED_FIELDS
        )

    @property
    def inherited(self) -> list[str]:
        """Fields carried through verbatim, in the app's own order."""
        return [
            f for f in self.order if f not in DERIVED_FIELDS and f not in RESET_FIELDS
        ]


def build_core(
    records: Sequence[dict[str, Any]],
    threshold: float = DEFAULT_CORE_THRESHOLD,
) -> Core:
    """Keep the fields present in >= ``threshold`` of app-written records.

    Values come from the most recently active record that has each field. On the
    machine this was validated against, a 0.9 threshold lands in a natural gap in
    the presence histogram (63/69 -> 43/69) and yields a 20-field core.

    With one record available the threshold degenerates to "every field in that
    record", which is the behaviour this replaced. It is never worse, and gets
    better as the app writes more records.
    """
    if not records:
        raise NoReferenceRecord()

    presence: dict[str, int] = {}
    for record in records:
        for field in record:
            presence[field] = presence.get(field, 0) + 1

    sample = len(records)
    keep = {
        field
        for field, count in presence.items()
        if count / sample >= threshold and field not in NEVER_INHERIT
    }

    ordered = sorted(records, key=lambda r: r.get("lastActivityAt") or 0, reverse=True)

    values: dict[str, Any] = {}
    for record in ordered:
        for field, value in record.items():
            if field in keep and field not in values:
                values[field] = value

    # Follow the newest record's field order so the result is shaped like
    # something the app wrote, with any stragglers appended.
    newest = ordered[0]
    order = [f for f in newest if f in values]
    order += [f for f in values if f not in newest]
    return Core(values=values, order=order, presence=presence, sample=sample)


def build_record(
    core: Core,
    cli_session_id: str,
    transcript: Transcript,
    session_id: str = "",
) -> dict[str, Any]:
    """Assemble one index record from the core plus derived fields."""
    record: dict[str, Any] = {}
    for field in core.order:
        record[field] = (
            RESET_FIELDS[field] if field in RESET_FIELDS else core.values[field]
        )

    title, source = session_title(transcript)
    record["sessionId"] = session_id or "local_" + str(uuid_mod.uuid4())
    record["cliSessionId"] = cli_session_id
    record["cwd"] = transcript.cwd
    record["originCwd"] = transcript.origin_cwd
    record["createdAt"] = to_epoch_ms(transcript.first_ts)
    record["lastActivityAt"] = to_epoch_ms(transcript.last_ts)
    record["lastFocusedAt"] = to_epoch_ms(transcript.last_ts)
    record["title"] = title
    record["titleSource"] = source
    record["completedTurns"] = transcript.turns
    return record
