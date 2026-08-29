# Roadmap

What is built, what is planned, and what will not be built. Ordered roughly by
value, not by effort.

The point of this repo is to stop being necessary. Everything below is written
on the assumption that the real fix is upstream — see [PROPOSAL.md](PROPOSAL.md)
— and that until then, the most useful thing this project can produce is
**evidence good enough to act on**.

---

## Shipped in 0.1

- [x] Rebuild the picker from CLI transcripts, dry-run by default
- [x] Clone the schema from a live record instead of hardcoding it
- [x] Honour deletion tombstones — the failure mode every other tool has
- [x] Filter subagent transcripts and workflow journals out of the picker
- [x] `customTitle` → `aiTitle` → first real message for titles
- [x] UTF-8 without a BOM at both ends
- [x] Windows Store / MSIX path probing

## Shipped in 0.2

- [x] **Validated on a second platform.** macOS, 69 app-written records against
      the original sample of 1. Four bugs found that n=1 could not show.
- [x] **Structural core** instead of cloning the richest record. The field set
      is conditional on what the session did, so no single record is a template.
- [x] **`completedTurns` settled** — user turns, scoped to the session's own id
- [x] **`originCwd` worktree rule** — 23/62 → 59/62 against real records
- [x] **`titleSource`** corrected to the app's own enum
- [x] **Account mismatch detection** and `--account`, for the two symptoms that
      look like bugs but are account scoping
- [x] **Python port** — no PowerShell needed on macOS or Linux
- [x] Packaged, 105 tests, CI on three platforms, enforced safety invariants
- [x] Tombstone gap verified across all four prior tools

## Next

- [ ] **A Linux column in SCHEMA.md.** Nobody has looked. The path probing is
      written and untested, which is the same position the macOS path was in
      before 0.2 — and that turned up four bugs.
- [ ] **Re-run the PowerShell script.** It carries every 0.2 correction but has
      not been executed since; the fixes were made on a machine without
      PowerShell. Highest-value single action in this list.
- [ ] **A `--verify` mode.** Re-derive every field for sessions that already
      have app-written records and print the agreement table, so anyone can
      reproduce the README's numbers on their own machine in one command.
      `tests/test_real_index.py` already does this; it should not need pytest.
- [ ] **Repair rather than only create.** [#63082](https://github.com/anthropics/claude-code/issues/63082)
      describes records losing `cliSessionId`. Restoring a field to an existing
      record is a different operation from writing a new one, and riskier — it
      would need its own backup discipline.
- [ ] **`transcriptUnavailable` handling.** Five records on the reference
      machine point at transcripts that are genuinely gone. Offering to clear
      those entries is plausible; deleting anything is not, so this would need
      to be explicitly opt-in.

## Under consideration

- [ ] **Archive/unarchive** rather than only `isArchived: false`.
- [ ] **A dry-run diff format** that is diffable between runs, so a user can see
      what an app update changed about their index.
- [ ] **Homebrew formula**, if the PyPI package sees real use.

## Out of scope

These will be declined regardless of implementation quality. Each one is a way
this tool could take something from a user that they cannot get back.

- **Writing to `~/.claude/projects/`.** Transcripts are the source of truth.
  Every recovery this tool performs depends on them being untouched. There is no
  feature worth making that assumption false.
- **Deleting index records.** The tool creates and never removes. A bug in a
  create path leaves clutter; a bug in a delete path is unrecoverable.
- **Ignoring tombstones by default.** `--include-deleted` exists and is opt-in.
  Flipping that default would resurrect sessions people deliberately removed.
- **Hardcoding the schema**, however tempting a stable-looking field list is.
  See SCHEMA.md — the list is not stable, and it is not even uniform within one
  machine.
- **Uploading anything.** No telemetry, no crash reporting, no "share your
  schema with us". The records contain project paths and session titles.
- **Touching the transcripts to fix a derivation.** If a title is wrong in the
  picker, the record is wrong, not the transcript.
