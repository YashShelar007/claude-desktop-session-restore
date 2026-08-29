"""Reading CLI transcripts and deriving record fields from them.

Transcripts are JSON Lines at ``~/.claude/projects/<encoded-cwd>/<uuid>.jsonl``.
Every derivation here was checked against records the app wrote for the same
sessions; the hit rates are in the README's validation table.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

# Harness scaffolding, skipped when falling back to the first user message for a
# title. These are not the human's words.
SCAFFOLD_RE = re.compile(
    r"^<(local-command|command-name|command-message|command-args"
    r"|system-reminder|user-prompt-submit)"
)

# A worktree session runs in <repo>/.claude/worktrees/<name>; the app records the
# repo root as originCwd and the worktree as cwd. 39 of 69 observed records have
# originCwd != cwd, almost all of them this shape. Deriving originCwd = cwd
# scored 23/62; this rule scores 59/62.
WORKTREE_RE = re.compile(r"^(.*)[/\\]\.claude[/\\]worktrees[/\\][^/\\]+")


@dataclass
class Transcript:
    """What a single transcript yields for the index record."""

    first_ts: str | None = None
    last_ts: str | None = None
    cwd: str | None = None
    custom_title: str | None = None
    ai_title: str | None = None
    first_user_msg: str | None = None
    turns: int = 0
    has_main_chain: bool = False

    @property
    def origin_cwd(self) -> str | None:
        return origin_of(self.cwd)


def origin_of(cwd: str | None) -> str | None:
    """The repo root behind a worktree path, or the path unchanged."""
    m = WORKTREE_RE.match(cwd or "")
    return m.group(1) if m else cwd


def _text_of(obj: dict[str, Any]) -> str:
    content = (obj.get("message") or {}).get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            b.get("text", "")
            for b in content
            if isinstance(b, dict) and b.get("type") == "text"
        )
    return ""


def _is_tool_result(obj: dict[str, Any]) -> bool:
    """A `user` line carrying tool output back to the model, not a human turn."""
    content = (obj.get("message") or {}).get("content")
    return isinstance(content, list) and any(
        isinstance(b, dict) and b.get("type") == "tool_result" for b in content
    )


def read_transcript(path: str, stem: str) -> Transcript | None:
    """Parse one transcript in a single pass.

    ``stem`` is the transcript's own session id (its filename stem). It matters:
    a resumed session's transcript carries the parent's lines forward, and those
    lines keep the parent's ``sessionId``. Without scoping to our own lines,
    ``createdAt`` lands on the parent's start date -- one observed session was 19
    days out -- and ``completedTurns`` counts the parent's turns too.

    Returns ``None`` only if the file cannot be opened. A malformed *line* is
    skipped, never fatal.
    """
    t = Transcript()
    own_first_ts: str | None = None
    own_cwd: str | None = None

    try:
        # errors="replace": a recovery tool must not refuse a transcript because
        # one byte is malformed.
        with open(path, encoding="utf-8", errors="replace") as handle:
            for raw in handle:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    obj = json.loads(raw)
                except ValueError:
                    continue

                if obj.get("isSidechain") is False:
                    t.has_main_chain = True

                kind = obj.get("type")
                if kind == "custom-title" and obj.get("customTitle"):
                    t.custom_title = obj["customTitle"]
                elif kind == "ai-title" and obj.get("aiTitle"):
                    t.ai_title = obj["aiTitle"]

                own = obj.get("sessionId") == stem

                timestamp = obj.get("timestamp")
                if timestamp:
                    if t.first_ts is None:
                        t.first_ts = timestamp
                    t.last_ts = timestamp
                    if own and own_first_ts is None:
                        own_first_ts = timestamp

                if obj.get("cwd"):
                    if t.cwd is None:
                        t.cwd = obj["cwd"]
                    if own and own_cwd is None:
                        own_cwd = obj["cwd"]

                if kind != "user" or obj.get("isSidechain"):
                    continue
                if _is_tool_result(obj):
                    continue

                text = _text_of(obj).strip()

                # completedTurns == human turns belonging to THIS session. isMeta
                # lines are harness bookkeeping; "[Request interrupted...]" is a
                # synthetic user line. Slash-command scaffolding DOES count --
                # excluding it drops the exact-match rate from 41/61 to 33/61.
                if (
                    own
                    and not obj.get("isMeta")
                    and not text.startswith("[Request interrupted")
                ):
                    t.turns += 1

                if t.first_user_msg is None and text and _is_human_prose(text):
                    t.first_user_msg = text
    except OSError:
        return None

    t.first_ts = own_first_ts or t.first_ts
    t.cwd = own_cwd or t.cwd
    return t


def _is_human_prose(text: str) -> bool:
    """Is this the user's own words, or harness scaffolding around them?"""
    return (
        not SCAFFOLD_RE.match(text)
        and not text.startswith("Caveat: The messages below were generated")
        and not text.startswith("[Request interrupted")
    )


def to_epoch_ms(iso: str | None) -> int | None:
    """ISO-8601 to epoch milliseconds. Naive timestamps are treated as UTC."""
    if not iso:
        return None
    text = iso.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        try:
            dt = datetime.strptime(text[:19], "%Y-%m-%dT%H:%M:%S").replace(
                tzinfo=timezone.utc
            )
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def session_title(t: Transcript) -> tuple[str, str]:
    """Returns ``(title, titleSource)``.

    customTitle (the user's own ``--name`` or ``/rename``) beats the model's
    aiTitle, which beats the first real user message. The app's own ``/desktop``
    import drops customTitle entirely -- anthropics/claude-code#83051.

    titleSource is the app's own enum and takes only "user" or "auto". Across 69
    observed records, all 24 with "user" have a title identical to the
    transcript's custom-title. "custom" is a value the app never writes.
    """
    if t.custom_title:
        return t.custom_title, "user"
    if t.ai_title:
        return t.ai_title, "auto"
    if t.first_user_msg:
        s = re.sub(r"\s+", " ", t.first_user_msg).strip()
        if len(s) > 60:
            s = s[:60].rstrip()
        return s, "auto"
    return "Untitled session", "auto"
