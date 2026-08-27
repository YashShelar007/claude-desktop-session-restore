# Proposal: let Claude Desktop adopt transcripts it didn't create

*Unofficial. Written against Claude Desktop `1.37937.3` / claude-code `2.1.246`,
on a Windows Store build and on macOS. Nothing here is an Anthropic position.*

## The problem in one line

Claude Desktop treats its session index as the source of truth for which
sessions exist, but the index is a **cache** — and there is no path that
rebuilds it from the transcripts, which are the actual durable data.

## Why it keeps getting reported

Same root cause, arriving by many routes:

| Route | Issues |
|---|---|
| CLI / VS Code sessions never appear | [#58670](https://github.com/anthropics/claude-code/issues/58670), [#50891](https://github.com/anthropics/claude-code/issues/50891), [#29331](https://github.com/anthropics/claude-code/issues/29331), #25524, #31787 |
| `.claude` moved to a new machine | [#69585](https://github.com/anthropics/claude-code/issues/69585), [#70312](https://github.com/anthropics/claude-code/issues/70312) |
| Reinstall / update | [#81907](https://github.com/anthropics/claude-code/issues/81907), [#85209](https://github.com/anthropics/claude-code/issues/85209), [#45710](https://github.com/anthropics/claude-code/issues/45710), [#29373](https://github.com/anthropics/claude-code/issues/29373) |
| Index entry damaged | [#56172](https://github.com/anthropics/claude-code/issues/56172), [#63082](https://github.com/anthropics/claude-code/issues/63082), [#63904](https://github.com/anthropics/claude-code/issues/63904) |

In every one of these the conversation data is **fine**. `claude --resume` finds
it. Only the UI can't see it. Several were closed as *not planned*, and at least
four community tools now exist to forge the missing records by hand — which
means users are writing undocumented internal state with scripts they found in a
gist. That is a worse outcome than either fixing it or documenting it.

## Root cause

`local_<uuid>.json` records are written only on the session-creation path. No
code reads `~/.claude/projects/` to discover transcripts lacking a record. A
cache miss is therefore indistinguishable from deletion, and the failure is
silent — the user sees an empty picker, not an error.

## Proposal

### 1. An adoption pass

On startup, and behind an explicit affordance in the picker
("Find existing sessions…"), enumerate transcripts with no index record and
materialise one.

Discovery rules, which matter more than they look:

- Only `<project>/<uuid>.jsonl`. Anything deeper is a subagent transcript or
  workflow journal. On two real machines that's the difference between 60
  entries and 233, and between 113 and 329.
- Skip names starting `agent-`.
- Require at least one line with `isSidechain: false`.
- **Skip anything with a `deleted_<cliSessionId>` tombstone.** Without this,
  adoption resurrects every session the user deliberately deleted — 39 of them
  on one of the machines checked, all with transcripts still on disk.
- Skip transcripts being actively written.

Field derivation is already proven — see [SCHEMA.md](SCHEMA.md). An independent
implementation was checked against 62 records the app wrote for the same
sessions: `createdAt` within 60 s on all 62, `originCwd` 59/62, `cwd` and
`title` 56/62.

Two derivations are worth calling out because a naive implementation gets them
wrong, and the app has the information to get them right for free:

- `originCwd` is the repo root, not `cwd`, for worktree sessions — 39 of 69
  records on one machine.
- Resumed sessions carry the parent's lines forward in the transcript, keeping
  the parent's `sessionId`. Counting or timestamping without filtering on the
  session's own id puts `createdAt` on the parent's start date (19 days out, in
  one observed case).

### 2. Make it cheap

The picker needs `title`, `cwd` and two timestamps. That does not require
parsing a whole transcript:

- `createdAt` is on the first line; `lastActivityAt` on the last. Seek, don't
  scan.
- `custom-title` / `ai-title` lines are small and rare.
- Cache derived metadata keyed by `(path, mtime, size)` so the pass is a no-op
  on subsequent launches.

Adoption then costs roughly one stat and two seeks per unindexed transcript,
once.

### 3. Stop dropping `customTitle` on import

[#83051](https://github.com/anthropics/claude-code/issues/83051): sessions
imported via `/desktop` always display "General coding session", even when the
CLI session has an explicit title from `--name` or `/rename`. The transcript
carries it on a `custom-title` line. 53 of 60 transcripts on one machine and 95
of 113 on another had one — so this isn't an edge case, it's the common path.
Same derivation as above; the fix is reading a field that's already on disk.

The index shows the app already knows how: `titleSource: "user"` exists and is
used correctly for titles set inside the app, on 24 of 69 records. The gap is
only that a CLI-side `/rename` never reaches the record.

### 4. Treat index damage as recoverable

Given adoption, [#63082](https://github.com/anthropics/claude-code/issues/63082)
(scanner strips `cliSessionId`, inserts `transcriptUnavailable: true`) stops
being data loss and becomes a cache miss that heals on next launch. Likewise a
reinstall. This is the real payoff: a whole class of "lost my history" reports
becomes self-correcting.

### Minimal version

If the full pass is too much, a single command — `/sessions adopt`, or a button
in the empty picker — captures most of the value. It turns "my history is gone"
into a documented recovery step, and removes the incentive to run a stranger's
script against internal app state.

## Things worth deciding explicitly

**Is the index a cache or a database?** If it's a cache, adoption follows and
tombstones are the only durable state that must not be reconstructed. If it's a
database, then it needs migration on upgrade, repair on corruption, and export
on machine change — all of which it currently lacks. The reports above are what
it looks like when this hasn't been decided.

**Should the format be documented?** Four community tools already write these
records. Documenting the format — even as explicitly unstable — is strictly
safer than the status quo, where the same work is redone from observation every
few months.

Worth documenting first: the field set is **conditional**, not fixed. 69 records
from one install carry 44 distinct fields across 44 distinct field-set
signatures. Every tool in this space assumes a fixed list, and the two obvious
strategies both fail — hardcoding a list writes fields sessions shouldn't have,
and cloning a live record copies its `prNumber`, its worktree path, and
potentially its `transcriptUnavailable: true` onto everything it writes.

**Artifacts across accounts.** Sessions migrated from a machine signed into a
different account show every artifact as unavailable, because artifacts are
server-side and account-scoped. Nothing local can fix this and the current
message — "not available or might be deleted" — reads as data loss when the data
is fine and simply belongs to another account. A clearer message ("published by
a different account") would save some confusion. The app has what it needs to
say so: the record's own position in the `<accountUuid>/<orgUuid>` tree.

## What was actually verified

- Forged records for 58 migrated sessions on a Windows Store install; all
  appeared in the picker and opened with full history.
- Derivation cross-checked against a record the app wrote for the same session.
- Tombstone pair mechanism (`deleted_<sessionId>` + `deleted_<cliSessionId>`,
  identical epoch-ms payloads) confirmed by correlating 10 UI deletions against
  what a restore pass wanted to recreate, then confirmed again at scale: 78
  tombstones on a second machine resolve into exactly 39 pairs, no singletons.
- `completedTurns` semantics, previously unverified: it counts **user** turns,
  not assistant lines. Sessions here run 19 turns against 784 assistant lines,
  3 against 531. See [SCHEMA.md](SCHEMA.md) for the exact predicate.
- Directory order is `<accountUuid>/<orgUuid>`, confirmed three independent
  ways. An earlier revision of SCHEMA.md claimed the reverse; that was wrong.
