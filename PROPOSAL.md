# Proposal: let Claude Desktop adopt transcripts it didn't create

*Unofficial. Written against Claude Desktop `1.37937.3` / claude-code `2.1.246`,
Windows Store build. Nothing here is an Anthropic position.*

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
  workflow journal. On one real machine that's the difference between 60 entries
  and 233.
- Skip names starting `agent-`.
- Require at least one line with `isSidechain: false`.
- **Skip anything with a `deleted_<cliSessionId>` tombstone.** Without this,
  adoption resurrects every session the user deliberately deleted.
- Skip transcripts being actively written.

Field derivation is already proven — see [SCHEMA.md](SCHEMA.md); an independent
implementation reproduced the app's own record to within a second.

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
carries it on a `custom-title` line. 53 of 60 transcripts on the machine tested
had one — so this isn't an edge case, it's the common path. Same derivation as
above; the fix is reading a field that's already on disk.

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
few months against a schema that has drifted six fields in under a year.

**Artifacts across accounts.** Sessions migrated from a machine signed into a
different account show every artifact as unavailable, because artifacts are
server-side and account-scoped. Nothing local can fix this and the current
message reads as data loss. A clearer message ("published by a different
account") would save some confusion.

## What was actually verified

- Forged records for 58 migrated sessions on a Windows Store install; all
  appeared in the picker and opened with full history.
- Derivation cross-checked against a record the app wrote for the same session.
- Tombstone pair mechanism (`deleted_<sessionId>` + `deleted_<cliSessionId>`,
  identical epoch-ms payloads) confirmed by correlating 10 UI deletions against
  what a restore pass wanted to recreate.
- Not verified: `completedTurns` semantics (`user` vs `assistant` line count) —
  the one available reference record had one of each.
