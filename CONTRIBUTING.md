# Contributing

This repo's product is **verified claims about an undocumented format**, not
code. The scripts are small; the value is in knowing which assertions are backed
by how much evidence. That shapes everything below.

## Branches

| Branch | What it means |
|---|---|
| `main` | Claims that have been verified. Cited from GitHub issues — treat it as published. |
| `develop` | Integration. New observations land here first. |

- `develop` is the **default branch**. Open PRs against it.
- `main` is protected: it only changes by PR from `develop`, tagged on merge.
- Maintainers push to `develop` directly for doc fixes and verified findings.
  Outside contributions come as PRs.

Releases are tagged when a **claim set** changes — a new platform column, a
retraction, a corrected derivation — not on a schedule. The version people cite
should match what they read.

## The claim standard

The most useful contribution to this repo is usually an observation, not a patch.
It is also where this repo has already been wrong twice, both times by
generalising from a single record. So:

**Every empirical claim carries its sample size and the build it came from.**

- Good: "`enabledMcpTools` present ⟹ `remoteMcpServersConfig` non-empty (43/43,
  macOS `1.37937.3`, n=69 records)"
- Not good: "newer builds dropped `enabledMcpTools`"

The second sentence is what this repo published for a while. It was wrong,
because n was 1 and the one record happened to have no MCP servers. A claim
without `n` and a build version doesn't merge.

If you can't reach n>1, say so in the text. "Observed once" is a fine claim.
"Changed between versions" is not, unless you have both versions.

## What's most wanted

1. **A Linux column.** [SCHEMA.md](SCHEMA.md) has Windows and macOS. Nobody has
   checked Linux at all.
2. **A different build.** Anything other than Desktop `1.37937.3` — especially
   the `2.1.144`–`2.1.145` range from
   [#63082](https://github.com/anthropics/claude-code/issues/63082).
3. **A machine with many app-written records.** The macOS pass found four bugs
   that were invisible at n=1. If your index has records, the numbers in the
   README's validation table can be reproduced against them.
4. **Counter-examples.** A record whose field set breaks the ≥90% core
   heuristic, or a tombstone that isn't part of a pair, is more valuable than
   another confirmation.

## Hard rules for anything that writes

These are not style preferences; each one corresponds to a way this has already
gone wrong.

- **Never write under `~/.claude/projects/`.** Transcripts are the source of
  truth and are read-only. Everything is recoverable as long as they are intact.
- **Dry-run is the default.** Writing requires an explicit flag.
- **Back up the index before writing**, and print the restore command.
- **Honour `deleted_*` tombstones.** See [SCHEMA.md](SCHEMA.md#tombstones) — all
  four prior tools resurrect deleted sessions because they don't.
- **Never write a UTF-8 BOM.** The app's parser rejects the record outright.
- **Don't inherit conditional fields.** The record's field set varies per
  session; cloning a live record copies its PR number and worktree path onto
  everything you write.

## Testing a change

There is no CI, because the thing under test is somebody's live app state. The
bar is:

```bash
python3 restore_desktop_sessions.py            # dry run, writes nothing
```

If you changed a derivation, validate it against records the app wrote for the
same sessions rather than against your own expectations — that is how every bug
in the last pass was found. The README's validation table shows the format.

The PowerShell and Python implementations must stay behaviourally identical.
If you change one, change the other, and say in the PR which one you actually ran.
