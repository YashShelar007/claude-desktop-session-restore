# The Claude Desktop session index, as observed

Everything here was established by observation against a live install. It is
**not documented by Anthropic** and can change in any update. Treat it as a
snapshot, not a contract.

Observed on:

| | |
|---|---|
| Claude Desktop | `1.37937.3` (Windows Store / MSIX, package `Claude_pzs8sxrjxfjjc`) |
| Bundled claude-code | `2.1.246` |
| Date | 2026-08-27 |

## Layout

```
<index-root>/
└── <orgUuid>/
    └── <accountUuid>/
        ├── local_<uuid>.json          one per session — the picker's entries
        ├── deleted_<uuid>             tombstone (no extension, see below)
        ├── scheduled-tasks.json       unrelated; routines/scheduled agents
        └── .restore-manifest.json     written by this tool, not the app
```

Note the order: **org first, then account.** #58670 documents it as
`<accountId>/<orgId>`; on the install tested here the outer directory matches
the org UUID in `ant-device-registry.json` and the inner one is the account.
Since both are opaque UUIDs the difference is invisible unless you cross-check,
and it doesn't matter to a tool that globs `*/*/`.

## The record

A real record, from a session the app itself created. Comments added.

```jsonc
{
  // identity
  "sessionId":      "local_012b0c4f-9bd1-4244-9e51-2e8946b3a64e", // = filename stem
  "cliSessionId":   "dfa14a0f-2a12-485c-b48e-b2c5ff69ecb9",       // = <stem>.jsonl transcript

  // where it ran
  "cwd":            "V:\\repos\\product",
  "originCwd":      "V:\\repos\\product",

  // epoch milliseconds
  "createdAt":      1787857878676,
  "lastActivityAt": 1787857881797,
  "lastFocusedAt":  1787857925473,   // re-stamped when the window regains focus

  // display
  "title":          "Greeting",
  "titleSource":    "auto",
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

### The bridge field

`cliSessionId` is the only thing tying a picker entry to a conversation. The
record holds no messages. Lose the field and the entry renders blank or reports
"session not found on disk"
([#56172](https://github.com/anthropics/claude-code/issues/56172),
[#63082](https://github.com/anthropics/claude-code/issues/63082)).

### Encoding

- **No BOM.** A UTF-8 BOM makes the app's parser reject the record.
- Written minified, UTF-8, no trailing newline.
- Non-ASCII in `title` is stored as raw UTF-8, not `\uXXXX` escapes.

## Version drift

The schema in [#58670](https://github.com/anthropics/claude-code/issues/58670),
captured from an earlier build, differs from the above:

| Field | #58670 (earlier build) | Observed here |
|---|---|---|
| `enabledMcpTools` | present, `{}` | **absent** |
| `lastFocusedAt` | absent | present |
| `spawnSeed` | absent | present |
| `reportFindingsCard` | absent | present |
| `lastSpawnRootDetected` | absent | present |
| `remoteControlAutoEligible` | absent | present |
| `model` | `claude-opus-4-7` | `claude-opus-5` |
| `effort` | `max` | `high` |
| `permissionMode` | `bypassPermissions` | `auto` |

Six fields differ in under a year. This is why hardcoding the field list is the
wrong approach, and why this tool clones a live record instead.

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

**Any tool that rebuilds the index must check for `deleted_<cliSessionId>`**
before writing a record, or it will resurrect every session the user ever
deleted. This was observed directly: 10 sessions deleted through the UI over a
three-minute window were queued for recreation on the next restore run.

## Deriving a record from a transcript

Transcripts are JSON Lines at
`~/.claude/projects/<encoded-cwd>/<cliSessionId>.jsonl`. Useful line types:

| Line type | Carries |
|---|---|
| `user` / `assistant` | `timestamp`, `cwd`, `isSidechain`, `sessionId`, `message` |
| `custom-title` | `customTitle` — set by `--name` or `/rename` |
| `ai-title` | `aiTitle` — the model's generated title |
| `summary`, `attachment`, `queue-operation`, … | not needed for the index |

Derivations that reproduced the app's own record to within a second:

| Record field | Derivation |
|---|---|
| `cliSessionId` | transcript filename stem |
| `cwd`, `originCwd` | first `cwd` seen in the transcript |
| `createdAt` | first `timestamp`, as epoch ms |
| `lastActivityAt` | last `timestamp`, as epoch ms |
| `lastFocusedAt` | last `timestamp` (the app re-stamps this on focus) |
| `title` | `customTitle` → `aiTitle` → first real user message, ~60 chars |
| `completedTurns` | count of `user` lines |

Validated against the app's own record for the same session: `cwd`,
`originCwd`, `title`, `completedTurns`, `model`, `effort` and `permissionMode`
matched exactly; `createdAt` was 0.8 s early and `lastActivityAt` 0.03 s early,
because the app stamps at session creation rather than at the first message.

**Unresolved:** `completedTurns` counting. This tool counts `user` lines;
lacique77's counts `assistant` lines. The single app-written record available
had one of each, so the sample can't distinguish them. A session with an
unequal count would settle it.

## What is *not* a session

`~/.claude/projects/` contains more than sessions. On the machine tested, 233
`.jsonl` files held 60 real sessions:

| Shape | Count | Real session? |
|---|---|---|
| `<project>/<uuid>.jsonl` | 60 | yes |
| `<project>/<uuid>/subagents/agent-*.jsonl` | 172 | no — subagent transcript |
| `<project>/<uuid>/subagents/workflows/*/journal.jsonl` | 1 | no — workflow journal |

Filter on all three of: top-level position only, name not starting `agent-`, and
at least one line with `isSidechain: false`.
