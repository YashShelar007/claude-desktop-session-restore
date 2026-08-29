# The Claude Desktop session index, as observed

Everything here was established by observation against live installs. It is
**not documented by Anthropic** and can change in any update. Treat it as a
snapshot, not a contract.

Observed on:

| | Windows | macOS |
|---|---|---|
| Claude Desktop | `1.37937.3` (Store / MSIX, package `Claude_pzs8sxrjxfjjc`) | `1.37937.3` (`~/Library/Application Support/Claude`) |
| Bundled claude-code | `2.1.246` | `2.1.246` |
| App-written records | 1 | **69** |
| Tombstones | 10 (one deletion burst) | **78** |
| Date | 2026-08-27 | 2026-08-27 |

The macOS install is the machine the Windows sessions were migrated *from*, so
the two observations describe the same session corpus seen through two builds.
With 69 app-written records instead of 1, macOS settles three things the Windows
sample could not.

## Layout

```
<index-root>/
└── <accountUuid>/
    └── <orgUuid>/
        ├── local_<uuid>.json          one per session — the picker's entries
        ├── deleted_<uuid>             tombstone (no extension, see below)
        ├── scheduled-tasks.json       unrelated; routines/scheduled agents
        └── .restore-manifest.json     written by this tool, not the app
```

**Correction.** An earlier revision of this file claimed the order was
`<orgUuid>/<accountUuid>` and that [#58670](https://github.com/anthropics/claude-code/issues/58670)
had it backwards. That was wrong; #58670 is right. The order is
**account first, then org.** Three independent confirmations on macOS:

- `~/.claude.json` → `oauthAccount.accountUuid` = the outer directory,
  `oauthAccount.organizationUuid` = the inner one.
- `config.json` → `lastKnownAccountUuid` = an outer directory name.
- Telemetry blobs in the app's Local Storage pair
  `"accountUuid":"<outer>"` with `"organizationUuid":"<inner>"`.

The original error came from reading `ant-device-registry.json` as a map of org
UUIDs. It is keyed by **account** UUID.

One account can appear under several orgs and vice versa; on the macOS machine
three accounts and three orgs produce five populated `<account>/<org>` folders.
A tool that globs `*/*/` is unaffected by the ordering, but anything that
reports which account owns a record is not.

## The record

A real record, from a session the app itself created (macOS). Comments added.

```jsonc
{
  // identity
  "sessionId":      "local_012b0c4f-9bd1-4244-9e51-2e8946b3a64e", // = filename stem
  "cliSessionId":   "dfa14a0f-2a12-485c-b48e-b2c5ff69ecb9",       // = <stem>.jsonl transcript

  // where it ran
  "cwd":            "/Users/you/repos/product",
  "originCwd":      "/Users/you/repos/product",

  // epoch milliseconds
  "createdAt":      1787857878676,
  "lastActivityAt": 1787857881797,
  "lastFocusedAt":  1787857925473,   // re-stamped when the window regains focus

  // display
  "title":          "Greeting",
  "titleSource":    "auto",          // "auto" | "user" — see below
  "isArchived":     false,
  "completedTurns": 1,

  // session config
  "model":          "claude-opus-5",
  "effort":         "high",
  "permissionMode": "auto",
  "chromePermissionMode": "skip_all_permission_checks",

  // per-session runtime state — do not copy between sessions
  "remoteMcpServersConfig":   [],   // can be a very large inlined tool blob
  "alwaysAllowedReasons":     [],
  "sessionPermissionUpdates": [],
  "spawnSeed":                {},

  // flags whose meaning is not established here
  "classifierSummaryEnabled":  true,
  "lastSpawnRootDetected":     false,
  "remoteControlAutoEligible": false,
  "reportFindingsCard":        true
}
```

### What has been checked on which platform

Not every claim here has two platforms behind it, and the difference matters.
The `real` test suite reports its sample size and skips rather than passing
vacuously, so this table reflects what was actually observed rather than what
happened to be green:

| Claim | macOS | Windows |
|---|---|---|
| Tombstones come in pairs | 78 files / 39 groups | **112 files / 56 groups** |
| Records carry no BOM | yes | **yes** |
| Records are minified, no trailing newline | yes | **yes** |
| `titleSource` ∈ {`auto`, `user`} | yes | **yes** |
| `enabledMcpTools` ⟹ non-empty `remoteMcpServersConfig` | 43/74 carriers | *no evidence* — no record there carries the field |
| Derivation hit rates | n=67 | *no evidence* — only 4 app-written records with transcripts |
| `completedTurns` predicate | n=67 | *no evidence* |

The Windows machine's index is mostly forged records from an older version of
this tool, which is why so little of it is usable as ground truth. That is a
property of that machine, not of Windows.

### Telling an app-written record from a forged one

Useful in two directions: auditing an index whose `.restore-manifest.json` is
missing or came from another tool, and stopping this project's own test suite
from grading its derivations against records it wrote earlier. It was doing
exactly that, and producing confident, meaningless failures.

Three signals. Each was checked against every known app-written record on the
reference machine and fired on **none** of them:

| Signal | App-written | Why |
|---|---|---|
| `lastFocusedAt == lastActivityAt` | 0/74 | the app re-stamps `lastFocusedAt` on window focus; a forging tool has nothing to re-stamp |
| `createdAt` == transcript's first timestamp, exactly | 0/67 | the app stamps at session creation, ~1.8 s *before* the first message |
| `lastActivityAt` == transcript's last timestamp, exactly | 0/67 | same shape — the app's value is its own, not the file's |

The `createdAt` margin is not marginal: across 67 records the delta never came
within 50 ms of zero (median +1.8 s, quartiles +1.1 s and +2.6 s). A forging
tool copies the timestamp straight out of the transcript, so it lands on zero
exactly.

The first signal is the weakest, and the reason the other two exist. **Opening a
forged session in the app clears it** — the app re-stamps `lastFocusedAt` on
focus, so a record that was forged and then opened looks app-written by that
test alone. On a machine where 58 sessions had been restored and then opened to
verify them, it caught 1 of 8. The timestamp signals catch the rest, because
`createdAt` is never re-stamped.

The failure mode in the other direction is a session the app created, never
re-focused, and happened to stamp with impossible precision. That costs a
sample rather than corrupting a result.

### The bridge field

`cliSessionId` is the only thing tying a picker entry to a conversation. The
record holds no messages. Lose the field and the entry renders blank or reports
"session not found on disk"
([#56172](https://github.com/anthropics/claude-code/issues/56172),
[#63082](https://github.com/anthropics/claude-code/issues/63082)).

### Encoding

Identical on both platforms, across all 69 macOS records:

- **No BOM.** A UTF-8 BOM makes the app's parser reject the record. 0/69 have one.
- Minified — no record contains a newline. 0/69 have a trailing newline.
- Non-ASCII in `title` is stored as raw UTF-8, not `\uXXXX` escapes.

### `titleSource`

Only two values occur in 69 records: `"auto"` (39) and `"user"` (24); 6 records
omit the field. **`"user"` is the value for a title the user set** — in all 24
cases the record's `title` is byte-identical to the transcript's `custom-title`
line, with no exceptions.

A tool writing `"custom"` here is writing a value the app never produces. (This
one did, until macOS showed the real enum.)

Note the converse does not hold: 30 records carry `titleSource: "auto"` even
though their transcript has a `custom-title` line, because the CLI-side rename
happened after the app stamped the record. The app does not re-read it.

## Field set: conditional, not fixed

This is the finding that changes the design argument, and it needed more than
one record to see.

**Every field ever reported for this record — all 19 in #58670, all 23 on
Windows — is present on macOS.** Nothing was removed by a version bump. What
looked like six fields of version drift is the same build writing different
fields for different sessions.

The 69 macOS records carry **44 distinct fields** in **44 distinct field-set
signatures**, ranging from 19 to 33 fields per record. There is a clean break in
how often each field occurs — 63/69 and up, then nothing until 43/69:

| Field | #58670 | Windows (n=1) | macOS (n=69) |
|---|---|---|---|
| `sessionId` `cliSessionId` `cwd` `originCwd` `createdAt` `lastActivityAt` `title` `isArchived` `model` `permissionMode` `remoteMcpServersConfig` `alwaysAllowedReasons` `sessionPermissionUpdates` | ✓ | ✓ | **69/69** |
| `lastFocusedAt` | — | ✓ | **69/69** |
| `spawnSeed` | — | ✓ | **69/69** |
| `chromePermissionMode` | ✓ | ✓ | 68/69 |
| `completedTurns` | ✓ | ✓ | 68/69 |
| `classifierSummaryEnabled` | ✓ | ✓ | 67/69 |
| `effort` | ✓ | ✓ | 67/69 |
| `titleSource` | ✓ | ✓ | 63/69 |
| ↑ *structural core — 20 fields* | | | ↑ |
| `enabledMcpTools` | ✓ | — | 43/69 |
| `sourceBranch` | — | — | 43/69 |
| `branch` | — | — | 42/69 |
| `prs` | — | — | 41/69 |
| `reportFindingsCard` | — | ✓ | 37/69 |
| `writtenBranches` | — | — | 33/69 |
| `promptSuggestion` | — | — | 26/69 |
| `prNumber` `prRepository` `prState` `prUrl` | — | — | 22/69 |
| `worktreeName` `worktreePath` | — | — | 16/69 |
| `sessionSettings` | — | — | 8/69 |
| `lastSpawnRootDetected` | — | ✓ | 7/69 |
| `transcriptUnavailable` | — | — | 5/69 |
| `remoteControlAutoEligible` | — | ✓ | 3/69 |
| `chromeTabGroupId` `dispatchParentOrigin` `forkedFromSessionId` | — | — | 2/69 |
| `color` `error` `errorAt` `spawnedFrom` | — | — | 1/69 |

`enabledMcpTools` is the clearest case. The README used to claim the Windows
build "does not write it at all." It writes it when the session has remote MCP
servers. Across 69 macOS records the implication is absolute:

> `enabledMcpTools` present ⟹ `remoteMcpServersConfig` non-empty (43/43, and
> 0 of the 5 records with an empty `remoteMcpServersConfig` carry it).

The Windows reference record has `"remoteMcpServersConfig": []`. It was never
going to have `enabledMcpTools`. That was not drift; that was a session with no
MCP servers.

Likewise `worktreeName`/`worktreePath` (16/69, and perfectly co-occurring),
the `pr*` family, `branch`/`sourceBranch`, and `forkedFromSessionId`: each
appears exactly when the session did the corresponding thing.

### Why this matters more than drift did

The original argument was "the schema drifts between versions, so don't
hardcode it — clone a live record." The drift is unproven. The conditionality
is proven, and it breaks *both* approaches:

**Hardcoding a field list** is wrong because there is no one field list. Any
list is either missing fields real sessions have, or inventing fields for
sessions that shouldn't have them.

**Cloning the richest live record** — what this tool did — is worse. The richest
record on this Mac has 33 fields, and cloning it stamps every restored session
with:

```
prNumber: 109, prUrl: ".../walnutech/frontend/pull/109", prState: "OPEN",
worktreePath: ".../.claude/worktrees/frontend-redesign-scope-a7edc9",
branch: "claude/frontend-redesign-scope-a7edc9",
promptSuggestion: "authorize miro and check if a design board exists",
enabledMcpTools: { ...1 KB of tool grants... }
```

Sixty sessions, all claiming to be on the same PR and the same worktree. And if
the chosen reference happens to carry `transcriptUnavailable: true` — 5 records
here do — every restored session is stamped broken on arrival.

### What to do instead

Clone the **structural core**, not a record. With more than one app-written
record available, compute it from the corpus rather than hardcoding:

1. Read every app-written record in the account folder.
2. Keep fields present in ≥90% of them. On this Mac that cut lands in the
   63→43 gap and yields exactly the 20-field core above.
3. Take values from the most recently active record, then override the derived
   fields and reset the per-session state fields.

With only one record available — the Windows case — the threshold degenerates
to "every field in that record," which is the old behaviour. The approach gets
strictly better as the app writes more records, and never gets worse.

A short denylist is still worth keeping for fields that are dangerous rather
than merely wrong to inherit (`transcriptUnavailable`, `error`, `errorAt`,
`forkedFromSessionId`, `spawnedFrom`), because a single-record machine cannot
tell they are conditional.

### `transcriptUnavailable`

5 records carry `transcriptUnavailable: true`, and in all 5 the transcript is
genuinely absent from `~/.claude/projects/`. On this build the flag is accurate
bookkeeping, not the destructive behaviour reported in
[#63082](https://github.com/anthropics/claude-code/issues/63082): `cliSessionId`
is **retained** on all 5. Two further records point at missing transcripts
without the flag, so it is not applied exhaustively.

## Tombstones

Deleting a session in the UI writes **two** extension-less files into the
account folder:

```
deleted_<desktop sessionId>     e.g. deleted_9e4b2416-b956-4e1a-8f3b-286e68e55033
deleted_<cliSessionId>          e.g. deleted_8cc9638e-7776-4e7c-93ce-5c9520b0c135
```

Each contains a single value: the deletion time in epoch milliseconds. Both
files in a pair carry the identical timestamp, which is what identifies them as
a pair.

The `local_*.json` record is removed at the same time; the transcript under
`~/.claude/projects/` is **not** touched.

Confirmed on both platforms, and it is the most thoroughly checked claim here.
**190 tombstone files resolve into 95 timestamp groups of exactly two — no
singletons, no groups of three, on either machine.**

| | tombstone files | groups | all pairs? |
|---|---|---|---|
| macOS | 78 | 39 | yes |
| Windows | 112 | 56 | yes |

Every payload is 13 bytes, digits only. In every macOS pair, exactly one member
is a `cliSessionId` with a transcript still on disk and the other is the desktop
`sessionId`, whose `local_*.json` is gone.

The Windows count also accounts for something that looked alarming. That index
holds only 8 records, on a machine where 58 sessions had been restored — a gap
that could plausibly have been the record-pruning behaviour reported in
[#63082](https://github.com/anthropics/claude-code/issues/63082). It isn't:
56 deletions plus 8 survivors is 64, which is the 58 restored plus the handful
the app created itself. The records were deleted, and the tombstones are the
receipt.

**Any tool that rebuilds the index must check for `deleted_<cliSessionId>`**
before writing a record, or it will resurrect every session the user ever
deleted. On the macOS machine that is 39 sessions whose transcripts are all
still present; on the Windows one, 56. Every one of them would come back.

Verified against the four community tools' current sources: none contains a
match for `deleted_`, `deleted` or `tombstone`.

| Tool | Tombstone check | Only skips | `completedTurns` |
|---|---|---|---|
| `lacique77/claude-sidebar-restore` | none | `cliSessionId` already indexed | count of `assistant` lines |
| `sahol3/claude-code-session-restorer` | none | `cliSessionId` already indexed | hardcoded `1` |
| `ibrews/claude-session-recovery` | none | already-registered ids | hardcoded `1` |
| `XPOL555`'s gist | none | already-registered ids | not written |

The failure is structural rather than careless: deletion removes the
`local_*.json` and leaves the transcript, so a deleted session presents exactly
as a never-indexed one. Only the tombstone distinguishes them.

The `completedTurns` column is worth reading alongside
[the section below](#completedturns-resolved) — four implementations, four
different answers, none matching what the app writes.

## Deriving a record from a transcript

Transcripts are JSON Lines at
`~/.claude/projects/<encoded-cwd>/<cliSessionId>.jsonl`. Useful line types:

| Line type | Carries |
|---|---|
| `user` / `assistant` | `timestamp`, `cwd`, `isSidechain`, `sessionId`, `isMeta`, `message` |
| `custom-title` | `customTitle` — set by `--name` or `/rename` |
| `ai-title` | `aiTitle` — the model's generated title |
| `summary`, `attachment`, `queue-operation`, … | not needed for the index |

| Record field | Derivation |
|---|---|
| `cliSessionId` | transcript filename stem |
| `cwd`, `originCwd` | first `cwd` seen in the transcript |
| `createdAt` | first `timestamp`, as epoch ms |
| `lastActivityAt` | last `timestamp`, as epoch ms |
| `lastFocusedAt` | last `timestamp` (the app re-stamps this on focus) |
| `title` | `customTitle` → `aiTitle` → first real user message, ~60 chars |
| `titleSource` | `"user"` if from `customTitle`, else `"auto"` |
| `completedTurns` | human turns — see below |

## `completedTurns`: resolved

Previously unresolved: this tool counted `user` lines, lacique77's counts
`assistant` lines, and the one Windows reference record had one of each.

**It is user turns.** 61 macOS records could be matched to their transcript, and
the assistant hypothesis is not close:

| Session | `completedTurns` | `user` lines | `assistant` lines |
|---|---|---|---|
| Social media marketing workflow | 19 | 360 | 784 |
| Console chats count and cap | 5 | 309 | 668 |
| Master | 3 | 237 | 531 |
| Matching v3 backend continuation | 21 | 581 | 1145 |

No scaling makes assistant counts land on `completedTurns`. But raw `user`
lines are just as wrong in the other direction — the current implementation
counts every `user` line, including tool results and sidechains, and matches
**1 of 61** records, with a median overcount of **21×** (one session records 287
turns against 3,141 `user` lines).

The metric that fits is *human turns belonging to this session*:

```
completedTurns = count of transcript lines where
      type == "user"
  AND isSidechain is not true
  AND sessionId == <cliSessionId>     // exclude lines inherited from a resumed parent
  AND message.content is not a tool_result
  AND isMeta is not true
  AND text does not begin with "[Request interrupted"
```

**42 of 61 exact, 50 of 61 within ±1, median delta 0.**

Two refinements earned their place. Slash-command scaffolding (`<command-name>`
and friends) *does* count as a turn — excluding it drops the exact match from 41
to 33. And the `sessionId` filter matters for resumed sessions, where the
transcript carries the parent's lines forward: the worst outlier, an
`Orchestrator` session with `completedTurns: 67`, has 206 human prompts in the
file but **68** whose `sessionId` is its own.

The residual ±1s are not explained by later CLI activity (15% of both the
matching and non-matching groups saw transcript writes after the record's
`lastActivityAt`). `completedTurns` is a counter the app maintains at runtime,
not a pure function of the transcript, so an exact reconstruction is not
available. It is cosmetic; derive it and move on.

## What is *not* a session

`~/.claude/projects/` contains more than sessions. Both machines agree on the
shape; only the counts differ.

| Shape | Windows | macOS | Real session? |
|---|---|---|---|
| `<project>/<uuid>.jsonl` | 60 | **113** | yes |
| `<project>/<uuid>/subagents/agent-*.jsonl` | 172 | **214** | no — subagent transcript |
| `<project>/<uuid>/subagents/workflows/*/journal.jsonl` | 1 | **2** | no — workflow journal |
| total | 233 | **329** | |

Filter on all three of: top-level position only, name not starting `agent-`, and
at least one line with `isSidechain: false`. On macOS all 113 top-level files
pass the third test, so position alone is doing the work — but the check costs
one line read and the app's own adoption pass would want it too.

## Accounts, orgs and artifacts

The index is partitioned by account, and this is what makes migrated artifacts
unavailable rather than merely unlinked.

On the macOS machine three accounts appear under `claude-code-sessions/`:

| Account UUID | Org UUID | Records | Identity |
|---|---|---|---|
| `744b1c86-…` | `9c8b154a-…` | **65** | `social@vantion.com` (per `~/.claude.json`) |
| `9c007fa7-…` | `c1c5b3ba-…` | 3 | a personal Google account |
| `69b16edc-…` | `869668d1-…` | 1 | currently signed in to the Desktop app |

The 65 migrated sessions, every transcript under `~/.claude/projects/`, and the
26 artifact UUIDs referenced across those transcripts all belong to account
`744b1c86`. The Desktop app on the same machine is signed in as `69b16edc`.

So the account split is not a Windows artifact — it is already present on the
machine the sessions came from, and it is visible in three places that agree:
`~/.claude.json` (`oauthAccount`), `config.json` (`lastKnownAccountUuid`), and
the app's own telemetry blobs.

Checked on the destination machine too. Its Desktop reports
`lastKnownAccountUuid` = `69b16edc-…` and its index contains exactly one account
folder, the same one — so every record the restore wrote there sits under an
account that authored none of those sessions. That is the whole mechanism:

| | authored the transcripts | signed in to Desktop |
|---|---|---|
| source machine (macOS) | `744b1c86-…` | `69b16edc-…` |
| destination machine (Windows) | — | `69b16edc-…` |

Artifacts belong to `744b1c86-…`; neither machine's app is showing that account.
Conversation history is unaffected on both, because it comes from the transcript
on disk.

Artifacts are server-side and keyed to the publishing account. Nothing in the
index, and nothing on disk, changes which account owns them — a restored session
will show its conversation in full and its artifacts as unavailable whenever the
signed-in account is not the one that published them. Where the record lives in
the `<account>/<org>` tree determines which account sees the entry at all;
it does not confer ownership of anything server-side.
