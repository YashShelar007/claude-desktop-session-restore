# claude-desktop-session-restore

Rebuild the Claude Desktop **Code** session picker from CLI transcripts, without
hardcoding an undocumented schema.

Claude Desktop lists Code sessions from small pointer records it keeps at:

```
<index-root>/<orgUuid>/<accountUuid>/local_<uuid>.json
```

Each record points at a CLI transcript through its `cliSessionId` field — the
filename stem of a `.jsonl` under `~/.claude/projects/`. The conversation itself
never lives in the record.

The app **only writes records for sessions it creates itself.** It never scans
`~/.claude/projects/` to adopt sessions that already exist. So CLI sessions, VS
Code sessions, and any `.claude` tree carried over from another machine are
invisible in the picker while every transcript sits intact on disk, resumable
from `claude --resume`.

This tool regenerates the missing records.

---

## Prior art

This is a well-trodden problem and this repo is not the first crack at it. If
you just want your sidebar back, any of these may serve you as well or better:

| Project | Platform | Notes |
|---|---|---|
| [lacique77/claude-sidebar-restore](https://github.com/lacique77/claude-sidebar-restore) | Win / mac / Linux | Python stdlib. The most portable. Already probes MSIX paths. |
| [sahol3/claude-code-session-restorer](https://github.com/sahol3/claude-code-session-restorer) | Windows | PowerShell, reversible, backs up the whole index. |
| [ibrews/claude-session-recovery](https://github.com/ibrews/claude-session-recovery) | — | Aimed at index corruption after crashes. |
| [XPOL555's gist](https://gist.github.com/XPOL555/1003cb862a88561dfad3f843f74de68f) | Windows | PowerShell, detects BOM corruption. |

The schema was first published in
[anthropics/claude-code#58670](https://github.com/anthropics/claude-code/issues/58670),
which demonstrated the fix by forging records for 207 sessions. That issue was
closed as *not planned*. It is a reopen of #29331, #25524, #29220 and #31787;
see also #50891, #69585, #70312, #81907 and #85209 for the same complaint
arriving by different routes.

There is now an official one-at-a-time path — `/desktop` in a CLI session, or
the `claude://resume?session=<uuid>` deep link — but it drops the session's
custom title ([#83051](https://github.com/anthropics/claude-code/issues/83051))
and does not help with a bulk migration.

## What this one adds

Four things the tools above don't do, each found by observing a live install:

**1. It refuses to invent a schema.**
Every other tool hardcodes a field list, and that list goes stale — sahol3's
README warns the format "may change between app versions," and it has. The
schema in #58670 carries `enabledMcpTools`, which the build tested here does not
write at all; that build instead writes `reportFindingsCard`,
`lastSpawnRootDetected`, `remoteControlAutoEligible` and `spawnSeed`, none of
which appear in #58670.

So this tool reads a record **the app itself wrote on this machine**, clones it,
and overrides only the ten fields it can derive. Unknown fields — including ones
added by a future update — are carried through verbatim. If no app-written
record exists it stops and tells you to create one, rather than guessing.

**2. It honours deletion tombstones.**
Deleting a session in the UI leaves a *pair* of files in the index folder,
`deleted_<desktop sessionId>` and `deleted_<cliSessionId>`, each containing only
a deletion timestamp in epoch milliseconds. No other tool appears to know these
exist — which means re-running one resurrects every session you have ever
deleted. Verified here: 10 sessions deleted through the UI were queued for
recreation until tombstone handling was added.

**3. It filters what a session actually is.**
`~/.claude/projects/` holds far more than sessions. On the machine this was
built against, 233 `.jsonl` files contained **60** real sessions; the other 173
were subagent transcripts (`agent-*`) and workflow journals nested under
`subagents/`. A tool that indexes every `.jsonl` it finds produces a picker with
173 junk entries. This one takes only top-level `<project>/<uuid>.jsonl`,
requires at least one line with `isSidechain: false`, and skips transcripts
still being written by a live session.

**4. It gets titles and encoding right.**
Titles prefer the user's own `custom-title` over the model's `ai-title` over the
first real user message, skipping harness scaffolding (`<command-name>`, caveat
blocks). 53 of 60 transcripts tested carried a `custom-title` — exactly what the
official `/desktop` import throws away.

On encoding, two separate traps: a UTF-8 **BOM** makes the app's JSON parser
reject the record outright, and PowerShell 5.1's `Get-Content` defaults to the
**ANSI codepage**, silently mojibaking any non-ASCII title on the way in. This
tool reads and writes UTF-8 explicitly at both ends.

## Usage

Requires PowerShell 5.1+ (ships with Windows). Claude Desktop must have run at
least once and created one Code session, so there is a record to model on.

```powershell
# Dry run - report what would be indexed, write nothing
.\Restore-DesktopSessions.ps1

# Cautious first pass: 5 most recent
.\Restore-DesktopSessions.ps1 -Limit 5 -Apply

# ...confirm they open with real history, then do the rest
.\Restore-DesktopSessions.ps1 -Apply

# After a migration where only some paths exist on this machine
.\Restore-DesktopSessions.ps1 -CwdPrefix 'V:\repos' -Apply
```

| Flag | Effect |
|---|---|
| `-Apply` | Write records. Without it, nothing is written. |
| `-Limit N` | Only the N most recently active. |
| `-CwdPrefix P` | Only transcripts whose recorded `cwd` starts with `P`. |
| `-IncludeDeleted` | Re-index tombstoned sessions. Off by default. |
| `-MinIdleMinutes N` | Skip transcripts touched in the last N minutes (default 2). |
| `-IndexDir` / `-ProjectsRoot` | Override auto-detection. |
| `-NoBackup` | Skip the pre-write backup. Not recommended. |

Every run with `-Apply` copies the whole index to
`claude-code-sessions_backup_<timestamp>` alongside it first, and prints the
restore command. Transcripts under `~/.claude/projects/` are **only ever read**.

### Where the index lives

Auto-detected, MSIX first:

| Install | Path |
|---|---|
| Windows Store / MSIX | `%LOCALAPPDATA%\Packages\Claude_*\LocalCache\Roaming\Claude\claude-code-sessions` |
| Windows direct | `%APPDATA%\Claude\claude-code-sessions` |
| macOS | `~/Library/Application Support/Claude/claude-code-sessions` |
| Linux | `$XDG_CONFIG_HOME/Claude/claude-code-sessions` |

The Store build is packaged, so its `%APPDATA%` writes are redirected into the
package container. Looking under plain `%APPDATA%` on a Store install finds
nothing and suggests the app has never run.

## Caveats

- **The format is undocumented and unstable.** Reverse-engineered by observation
  against Claude Desktop `1.37937.3` / bundled claude-code `2.1.246`. It can
  change in any update. That's the reason for the clone-a-live-record design,
  but it isn't a guarantee.
- **Artifacts won't follow across accounts.** Artifacts are server-side and
  keyed to the account that published them. Sessions migrated from a machine
  signed into a different account will show their artifacts as unavailable, and
  nothing on disk can change that. Conversation history is unaffected.
- **Some builds prune records.**
  [#63082](https://github.com/anthropics/claude-code/issues/63082) reports a
  startup scanner in Desktop 2.1.144–2.1.145 stripping `cliSessionId` and
  inserting `transcriptUnavailable: true` on every launch. Not observed on the
  build tested here.
- Unofficial and unaffiliated with Anthropic.

## See also

- [SCHEMA.md](SCHEMA.md) — the record format, field by field, with version notes
- [PROPOSAL.md](PROPOSAL.md) — a proposed upstream fix

## License

MIT
