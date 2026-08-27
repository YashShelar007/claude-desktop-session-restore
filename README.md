# claude-desktop-session-restore

Rebuild the Claude Desktop **Code** session picker from CLI transcripts, without
hardcoding an undocumented schema.

Claude Desktop lists Code sessions from small pointer records it keeps at:

```
<index-root>/<accountUuid>/<orgUuid>/local_<uuid>.json
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

Four things the tools above don't do, each found by observing live installs —
one Windows, one macOS with 69 app-written records to check against.

**1. It refuses to invent a schema, because there isn't one.**
Every other tool hardcodes a field list. The 69 macOS records carry **44
distinct fields in 44 distinct field-set signatures**, from 19 to 33 fields per
record. There is no single correct list, because the field set is *conditional
on what the session did*: `worktreeName`/`worktreePath` only for worktree
sessions, the `pr*` family only where a PR was opened, `enabledMcpTools` only
where remote MCP servers were configured (43/43 records that have it have a
non-empty `remoteMcpServersConfig`; none of the 5 with an empty one do).

That also kills the version-drift story this README used to tell. Every field
ever reported for this record — all of #58670's, all of the Windows build's — is
present on macOS. Nothing was removed by an update; different sessions get
different fields.

Cloning the richest live record, which is what this tool used to do, is worse
than hardcoding. The richest record on the macOS machine would stamp all 60
restored sessions with `prNumber: 109`, someone else's worktree path, a stale
`promptSuggestion` and 1 KB of MCP tool grants — and if the chosen record
carried `transcriptUnavailable: true` (5 do), every restored session would be
marked broken on arrival.

So this tool reads **every record the app wrote on this machine**, keeps the
fields present in ≥90% of them, and takes values from the most recent. On the
macOS machine that threshold lands in a natural gap (63/69 → 43/69) and yields
a 20-field structural core. With only one app-written record it degenerates to
"every field in that record" — the old behaviour — so it is never worse and gets
better as the app writes more. If no app-written record exists it stops and
tells you to create one, rather than guessing.

**2. It honours deletion tombstones.**
Deleting a session in the UI leaves a *pair* of files in the index folder,
`deleted_<desktop sessionId>` and `deleted_<cliSessionId>`, each containing only
a deletion timestamp in epoch milliseconds.

**None of the four tools listed above looks for them** — no match for
`deleted_`, `deleted` or `tombstone` in any of their sources, checked against
current `HEAD`. Each skips only sessions whose `local_*.json` is currently
present, but deletion removes that file and leaves the transcript, so a deleted
session is indistinguishable from a never-indexed one. Re-running any of them
after deleting sessions in the UI brings them all back. The macOS machine confirms the mechanism at scale: **78 tombstones
resolve into exactly 39 timestamp pairs, no singletons, no groups of three**,
and a run without tombstone handling would bring back all 39 deleted sessions,
whose transcripts are all still on disk.

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
blocks). 53 of 60 transcripts on Windows and **95 of 113 on macOS** carried a
`custom-title` — exactly what the official `/desktop` import throws away.

`titleSource` takes the app's own enum, `"user"` or `"auto"`. All 24 macOS
records with `titleSource: "user"` have a title byte-identical to their
transcript's `custom-title`. (This tool used to write `"custom"`, a value the
app never produces.)

On encoding, two separate traps: a UTF-8 **BOM** makes the app's JSON parser
reject the record outright, and PowerShell 5.1's `Get-Content` defaults to the
**ANSI codepage**, silently mojibaking any non-ASCII title on the way in. This
tool reads and writes UTF-8 explicitly at both ends.

## Usage

Two implementations, same behaviour and same flags. Claude Desktop must have run
at least once and created one Code session, so there is a record to model on.

**Python 3.8+, stdlib only** — no PowerShell needed on macOS or Linux:

```bash
# Dry run - report what would be indexed, write nothing
python3 restore_desktop_sessions.py

# Cautious first pass: 5 most recent
python3 restore_desktop_sessions.py --limit 5 --apply

# ...confirm they open with real history, then do the rest
python3 restore_desktop_sessions.py --apply

# After a migration where only some paths exist on this machine
python3 restore_desktop_sessions.py --cwd-prefix /Users/you/repos --apply
```

**PowerShell 5.1+** (ships with Windows):

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
| `-CoreThreshold F` | Keep fields present in ≥F of app-written records (default 0.9). |
| `-Account UUID` | Write into this account's folder instead of the one with the most records. |
| `-NoBackup` | Skip the pre-write backup. Not recommended. |

The Python flags are the same names in lower kebab-case: `--apply`, `--limit`,
`--cwd-prefix`, `--include-deleted`, `--min-idle-minutes`, `--index-dir`,
`--projects-root`, `--core-threshold`, `--account`, `--no-backup`.

### Accounts

The index is partitioned by account, and records are only visible to the account
whose folder they are in. Both scripts read the two signals that reveal a
mismatch and warn before writing:

- `~/.claude.json` → `oauthAccount.accountUuid` — who authored the transcripts,
  and therefore who owns any artifacts those sessions published
- `config.json` → `lastKnownAccountUuid` — which account the picker is showing

If the target folder disagrees with either, you get a warning explaining which
symptom to expect. Use `--account` / `-Account` to target a specific one.

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

- **The format is undocumented.** Reverse-engineered by observation against
  Claude Desktop `1.37937.3` / bundled claude-code `2.1.246`, on a Windows Store
  build and on macOS. It can change in any update. The structural-core design is
  a hedge against that, not a guarantee.
- **The PowerShell and Python paths are not equally tested.** The Python script
  was validated against 62 app-written macOS records (see below). The PowerShell
  script carries the same corrections but has not been re-run since — there was
  no PowerShell on the macOS machine the fixes were made on.
- **Artifacts won't follow across accounts.** Artifacts are server-side and
  keyed to the account that published them. Sessions migrated from a machine
  signed into a different account will show their artifacts as unavailable, and
  nothing on disk can change that. Conversation history is unaffected.

  Confirmed from both ends, not inferred. On the source machine the sessions,
  transcripts and all 26 referenced artifact UUIDs belong to account **A**
  (`~/.claude.json` → `oauthAccount`). The destination machine's Desktop is
  signed in as account **B** (`config.json` → `lastKnownAccountUuid`), and its
  index contains exactly one account folder — B's. So all restored records live
  under an account that never authored those sessions. History restores because
  it is read from the local transcript; artifacts don't because they are
  resolved server-side against the signed-in account.

  This leaves a choice rather than a bug, and it is worth knowing before you
  run anything:

  | Signed in as | Sessions in the picker | Artifacts |
  |---|---|---|
  | B (where the records were written) | yes | unavailable |
  | A (which owns the artifacts) | no — wrong account folder | resolve |

  To get both, restore into A's folder with `--account A` while signed in as A.
  Both scripts now detect and warn about each half of this.
- **Some builds prune records.**
  [#63082](https://github.com/anthropics/claude-code/issues/63082) reports a
  startup scanner in Desktop 2.1.144–2.1.145 stripping `cliSessionId` and
  inserting `transcriptUnavailable: true` on every launch. The destructive part
  is not observed here: 5 macOS records carry `transcriptUnavailable: true` and
  all 5 keep their `cliSessionId`, and in all 5 the transcript really is gone
  from disk. The flag is accurate bookkeeping on this build — but it is exactly
  the kind of field a tool must not inherit when cloning.
- **`completedTurns` is approximate.** It is a counter the app maintains at
  runtime, not a pure function of the transcript, so it cannot be reconstructed
  exactly. The derivation here matches 42 of 61 records exactly and 50 within
  ±1. It is cosmetic.
- Unofficial and unaffiliated with Anthropic.

## How the derivations were checked

Every field is derived from the transcript and compared against the record the
app wrote for the *same* session. 62 macOS sessions could be matched:

| Field | Agrees with the app's own record |
|---|---|
| `createdAt` | 62/62 within 60 s (median +2.0 s — the app stamps at session creation, before the first message) |
| `originCwd` | 59/62 |
| `cwd` | 56/62 |
| `title` | 56/62 |
| `lastActivityAt` | 52/62 within 60 s (the rest are transcripts that kept growing after the app stopped tracking) |
| `completedTurns` | 42/61 exact, 50/61 within ±1 |
| `titleSource` | 29/59 |

Two of these deserve a note.

`titleSource` disagrees on 30 records where the app says `"auto"` but the
transcript has a `custom-title`. That is not a derivation bug — it is the app
failing to re-read a title the user set later from the CLI, which is
[#83051](https://github.com/anthropics/claude-code/issues/83051) seen from the
other side. For a session the app never indexed, `custom-title` is the right
answer.

`originCwd` was the single worst bug found: the tool used to set it equal to
`cwd`, which is correct for 30 of 69 records and wrong for the 39 worktree
sessions where the app records the repo root. Deriving it from the
`.claude/worktrees/<name>` path segment took it from 23/62 to 59/62.

## See also

- [SCHEMA.md](SCHEMA.md) — the record format, field by field, with the macOS/Windows/#58670 comparison
- [PROPOSAL.md](PROPOSAL.md) — a proposed upstream fix

## License

MIT
