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

## Scope: what will be declined

This tool creates index records and never removes them, and it never writes
anywhere near the transcripts. Those two properties are why it is safe to run on
a machine where something has already gone wrong. PRs that change them will be
declined regardless of quality:

- **No writes under `~/.claude/projects/`.** Transcripts are the source of
  truth. Every recovery this tool performs depends on them being untouched.
- **No deleting index records.** A bug in a create path leaves clutter. A bug in
  a delete path is unrecoverable.
- **No network.** No telemetry, no crash reporting, no schema upload. Records
  contain project paths and session titles.

The full list, with reasons, is in [ROADMAP.md](ROADMAP.md#out-of-scope).

## Hard rules

Each corresponds to a way this has already gone wrong, or would. Most are
enforced, not just documented — `scripts/check_invariants.py` runs in CI and
locally, and fails the build if a refactor drops one while keeping tests green.

- **Never write under `~/.claude/projects/`.** Pinned by
  `check_transcripts_are_read_only` and by
  `tests/test_cli.py::TestTranscriptsAreReadOnly`, which hashes the whole tree
  before and after an `--apply` run.
- **Dry run is the default.** `--apply` is opt-in. Pinned by
  `check_dry_run_is_the_default`.
- **Back up the index before writing**, not after, and print the restore
  command. Pinned by `check_backup_precedes_write`.
- **Honour `deleted_*` tombstones.** See [SCHEMA.md](SCHEMA.md#tombstones) — all
  four prior tools in this space resurrect deleted sessions because they don't.
  Pinned by `check_tombstones_are_honoured` and `tests/test_cli.py`.
- **Never write a UTF-8 BOM.** The app's parser rejects the record outright.
  `utf-8-sig` is correct for reading and wrong for writing. Pinned by
  `check_no_bom_on_write`.
- **Don't inherit conditional fields.** The field set varies per session;
  cloning a live record copies its PR number, its worktree path and possibly
  `transcriptUnavailable: true` onto everything you write. Pinned by
  `tests/test_records.py`.
- **Don't add a field the app didn't write.** Reset fields are reset only if the
  structural core already carries them.

## Testing a change

```bash
pip install -e ".[dev]"
pytest -q                                   # 105 tests, no real state touched
ruff check src tests restore_desktop_sessions.py
python3 scripts/check_invariants.py
```

Everything above runs against fixtures in a temp dir. Tests that read this
machine's actual Claude state are marked `real` and excluded by default:

```bash
pytest -m real      # needs a real index and ~/.claude/projects; writes nothing
```

The `real` suite is the important one when you touch a derivation. It compares
every derived field against the record the app itself wrote for the *same*
session, which is the only check that has ever caught a real bug here — four of
them, all invisible against a single hand-picked record. It is also what
produces the README's validation table.

If you change a documented number, change the README in the same PR and say
which command you ran. A number in the docs that no test reproduces is a bug.

**The two implementations must stay behaviourally identical.** If you change
`src/claude_desktop_restore/`, change `Restore-DesktopSessions.ps1` too, and say
in the PR which one you actually executed. They are currently not equally
tested — see the caveat in the README.

## Releasing

```bash
# on develop, with SCHEMA.md and the version in pyproject.toml both current
gh pr create --base main --head develop
# once merged:
git checkout main && git pull
git tag v0.2.1 && git push origin v0.2.1
```

The release workflow refuses to publish if the tag does not match the version in
`pyproject.toml`, so bump that in the same PR.

Uploading to PyPI is gated on a repository variable, so a tag can be pushed
before PyPI is configured — and a fork can cut its own tags without credentials
— without either producing a failed run. The workflow builds and verifies the
artifact either way, and says out loud when it skipped the upload:

```bash
gh variable set PUBLISH_TO_PYPI --body true
```

The one-time PyPI Trusted Publisher setup it needs first is documented at the
top of [`.github/workflows/release.yml`](.github/workflows/release.yml).
