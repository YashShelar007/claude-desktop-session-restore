## What this changes

<!-- One or two sentences. -->

## If this changes a documented claim

SCHEMA.md and the README state things as measured facts, with sample sizes.
If this PR changes one, say so here:

- **Claim affected:**
- **New evidence:** platform, Claude Desktop build, and `n`
- **What it replaces:** quote the line you are retracting, if any

Retractions are welcome — this repo has published wrong claims twice and both
came from generalising a single record.

## Checklist

- [ ] `pytest -q` passes
- [ ] `ruff check src tests restore_desktop_sessions.py` is clean
- [ ] `python3 scripts/check_invariants.py` passes
- [ ] If a derivation changed, I ran `pytest -m real` on a machine with a real
      index, or said explicitly that I could not
- [ ] If I changed one implementation, I changed the other
      (`src/claude_desktop_restore/` and `Restore-DesktopSessions.ps1`), and
      said below which one I actually ran
