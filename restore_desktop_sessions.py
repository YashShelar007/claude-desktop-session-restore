#!/usr/bin/env python3
"""
Rebuild the Claude Desktop "Code" session index from CLI transcripts.

Claude Desktop lists Code sessions from per-session pointer records at
    <index-root>/<accountUuid>/<orgUuid>/local_<uuid>.json
Each record points at a CLI transcript through its "cliSessionId" field. The app
only writes records for sessions IT creates, so CLI sessions -- and any session
tree migrated from another machine -- stay invisible in the picker even though
every transcript is intact on disk.

This script regenerates the missing records.

It does NOT hardcode the record schema. The record's field set is conditional on
what a session did -- worktree fields only for worktree sessions, pr* only where
a PR was opened, enabledMcpTools only where remote MCP servers were configured
-- so no single record is a valid template. Instead this reads every record the
app wrote on THIS machine and keeps the fields that appear in almost all of
them (the structural core), taking values from the most recent one. Fields added
by a future app update are carried through automatically; per-session state is
left behind.

Stdlib only. Python 3.8+. See SCHEMA.md.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import platform
import re
import shutil
import sys
import time
import uuid
from datetime import datetime, timezone

# --------------------------------------------------------------------- config

# Derived per session; never inherited from the reference.
DERIVED_FIELDS = {
    "sessionId", "cliSessionId", "cwd", "originCwd",
    "createdAt", "lastActivityAt", "lastFocusedAt",
    "title", "titleSource", "completedTurns",
}

# Per-session runtime state. Reset to an empty value, but only if the core
# actually carries the field.
RESET_FIELDS = {
    "remoteMcpServersConfig": [],
    "alwaysAllowedReasons": [],
    "sessionPermissionUpdates": [],
    "spawnSeed": {},
    "isArchived": False,
}

# Fields that are dangerous rather than merely wrong to inherit. The presence
# threshold already excludes these on a machine with many records; this is the
# backstop for a machine that has only one, where conditionality is invisible.
NEVER_INHERIT = {
    "transcriptUnavailable",   # marks the session broken on arrival
    "error", "errorAt",
    "forkedFromSessionId", "spawnedFrom", "dispatchParentOrigin",
    "prNumber", "prUrl", "prRepository", "prState", "prs",
    "branch", "sourceBranch", "writtenBranches",
    "worktreeName", "worktreePath",
    "promptSuggestion", "chromeTabGroupId", "color",
    "enabledMcpTools",         # paired with remoteMcpServersConfig, which we reset
}

# A worktree session runs in <repo>/.claude/worktrees/<name>; the app records the
# repo root as originCwd and the worktree as cwd. 39 of 69 observed records have
# originCwd != cwd, almost all of them this shape.
WORKTREE_RE = re.compile(r"^(.*)[/\\]\.claude[/\\]worktrees[/\\][^/\\]+")


def origin_of(cwd):
    m = WORKTREE_RE.match(cwd or "")
    return m.group(1) if m else cwd


SCAFFOLD_RE = re.compile(
    r"^<(local-command|command-name|command-message|command-args"
    r"|system-reminder|user-prompt-submit)"
)

MANIFEST = ".restore-manifest.json"


def step(m): print("==> %s" % m)
def ok(m):   print("  + %s" % m)
def warn(m): print("  ! %s" % m)


# ---------------------------------------------------------------- path probing

def candidate_index_roots():
    home = os.path.expanduser("~")
    c = []
    la = os.environ.get("LOCALAPPDATA")
    if la:
        # Store / MSIX install: %APPDATA% writes are redirected into the package
        # container, so probe this BEFORE the plain path.
        for pkg in sorted(glob.glob(os.path.join(la, "Packages", "Claude_*"))):
            c.append(os.path.join(pkg, "LocalCache", "Roaming", "Claude",
                                  "claude-code-sessions"))
    ad = os.environ.get("APPDATA")
    if ad:
        c.append(os.path.join(ad, "Claude", "claude-code-sessions"))
    if platform.system() == "Darwin":
        c.append(os.path.join(home, "Library", "Application Support", "Claude",
                              "claude-code-sessions"))
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        c.append(os.path.join(xdg, "Claude", "claude-code-sessions"))
    c.append(os.path.join(home, ".config", "Claude", "claude-code-sessions"))
    c.append(os.path.join(home, "Library", "Application Support", "Claude",
                          "claude-code-sessions"))
    seen, out = set(), []
    for p in c:
        if p not in seen:
            seen.add(p); out.append(p)
    return out


def resolve_index_root(override):
    if override:
        if not os.path.isdir(override):
            sys.exit("IndexDir not found: %s" % override)
        return os.path.abspath(override)
    cands = candidate_index_roots()
    for p in cands:
        if os.path.isdir(p):
            return p
    sys.exit(
        "No Claude Desktop session index found. Probed:\n  " +
        "\n  ".join(cands) +
        "\n\nIf the app has never run here, open it, start one Code session, send a\n"
        "message, let it finish, then re-run. This tool needs app-written records\n"
        "to model the schema on."
    )


def read_account_signals(index_root):
    """Which account authored the transcripts, and which one is the app showing?

    Two different questions, two different files, and they can disagree --
    which is exactly the confusing case this warns about.

    - ~/.claude.json oauthAccount.accountUuid is the account the CLI is signed
      in as, so it owns the transcripts under ~/.claude/projects/ and anything
      those sessions published server-side (artifacts).
    - config.json lastKnownAccountUuid is the account the Desktop app is
      showing. The picker only reads that account's folder.
    """
    cli_account = cli_org = app_account = None
    try:
        with open(os.path.join(os.path.expanduser("~"), ".claude.json"),
                  encoding="utf-8-sig") as f:
            oa = json.load(f).get("oauthAccount") or {}
        cli_account = oa.get("accountUuid")
        cli_org = oa.get("organizationUuid")
    except Exception:
        pass
    try:
        with open(os.path.join(os.path.dirname(index_root), "config.json"),
                  encoding="utf-8-sig") as f:
            app_account = json.load(f).get("lastKnownAccountUuid")
    except Exception:
        pass
    return cli_account, cli_org, app_account


def warn_account_mismatch(account, cli_account, app_account):
    """Two failure modes that look like bugs but are account scoping."""
    if app_account and app_account != account:
        warn("The Desktop app is signed in as a DIFFERENT account:")
        print("      writing into : %s" % account)
        print("      app shows    : %s" % app_account)
        print("      Records written here are correct but will not appear in the")
        print("      picker until you sign in as the first account. Use --account")
        print("      to target the signed-in one instead.")
    if cli_account and cli_account != account:
        warn("The transcripts were authored by a DIFFERENT account:")
        print("      writing into : %s" % account)
        print("      authored by  : %s" % cli_account)
        print("      Conversation history will restore in full -- it is read from")
        print("      the local transcript. Artifacts published in these sessions")
        print("      are server-side and account-scoped, so they will show as")
        print("      unavailable. Nothing on disk can change that.")


def resolve_account_dir(index_root, want_account=None):
    """Layout is <accountUuid>/<orgUuid>/local_*.json -- account first."""
    pairs = []
    for acct in sorted(glob.glob(os.path.join(index_root, "*", "*"))):
        if not os.path.isdir(acct):
            continue
        n = len(glob.glob(os.path.join(acct, "local_*.json")))
        pairs.append((n, acct))
    if not pairs:
        sys.exit("No <accountUuid>/<orgUuid> folder pair under %s.\n"
                 "Start a session in the app first." % index_root)
    if want_account:
        match = [p for _, p in pairs if p.split(os.sep)[-2] == want_account]
        if not match:
            sys.exit("No folder for account %s under %s.\nFound: %s"
                     % (want_account, index_root,
                        ", ".join(sorted({p.split(os.sep)[-2] for _, p in pairs}))))
        return match[0]

    pairs.sort(key=lambda x: -x[0])
    if len(pairs) > 1:
        warn("Multiple account/org folders found; using the one with the most records:")
        for n, p in pairs:
            a, o = p.split(os.sep)[-2:]
            print("      %s/%s  (%d records)" % (a, o, n))
    return pairs[0][1]


# ------------------------------------------------------------- transcript read

def _text_of(obj):
    msg = obj.get("message") or {}
    c = msg.get("content")
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return "\n".join(b.get("text", "") for b in c
                         if isinstance(b, dict) and b.get("type") == "text")
    return ""


def _is_tool_result(obj):
    c = (obj.get("message") or {}).get("content")
    return isinstance(c, list) and any(
        isinstance(b, dict) and b.get("type") == "tool_result" for b in c)


def read_transcript(path, stem):
    """One pass. Returns everything the record needs, or None if unreadable."""
    first_ts = last_ts = cwd = custom_title = ai_title = first_user_msg = None
    own_first_ts = own_cwd = None
    turns = 0
    has_main_chain = False

    try:
        f = open(path, encoding="utf-8", errors="replace")
    except OSError:
        return None
    with f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                o = json.loads(line)
            except ValueError:
                continue

            if o.get("isSidechain") is False:
                has_main_chain = True
            t = o.get("type")
            if t == "custom-title" and o.get("customTitle"):
                custom_title = o["customTitle"]
            elif t == "ai-title" and o.get("aiTitle"):
                ai_title = o["aiTitle"]

            # A resumed session's transcript carries the parent's lines forward.
            # They keep the parent's sessionId, and the app never saw them --
            # scoping to our own lines is what keeps createdAt off the parent's
            # start date (one session here was 19 days out).
            own = o.get("sessionId") == stem

            ts = o.get("timestamp")
            if ts:
                if first_ts is None:
                    first_ts = ts
                last_ts = ts
                if own and own_first_ts is None:
                    own_first_ts = ts
            if o.get("cwd"):
                if cwd is None:
                    cwd = o["cwd"]
                if own and own_cwd is None:
                    own_cwd = o["cwd"]

            if t != "user" or o.get("isSidechain"):
                continue
            if _is_tool_result(o):
                continue

            txt = _text_of(o).strip()

            # completedTurns == human turns belonging to THIS session.
            # - isMeta lines are harness bookkeeping, not turns
            # - "[Request interrupted...]" is a synthetic user line
            # - a resumed session's transcript carries the parent's lines
            #   forward; they keep the parent's sessionId and were never
            #   counted by the app
            # Slash-command scaffolding DOES count -- see SCHEMA.md.
            if (own
                    and not o.get("isMeta")
                    and not txt.startswith("[Request interrupted")):
                turns += 1

            if first_user_msg is None and txt:
                if (not SCAFFOLD_RE.match(txt)
                        and not txt.startswith("Caveat: The messages below were generated")
                        and not txt.startswith("[Request interrupted")):
                    first_user_msg = txt

    return {
        "first_ts": own_first_ts or first_ts, "last_ts": last_ts,
        "cwd": own_cwd or cwd,
        "custom_title": custom_title, "ai_title": ai_title,
        "first_user_msg": first_user_msg,
        "turns": turns, "has_main_chain": has_main_chain,
    }


def to_epoch_ms(iso):
    if not iso:
        return None
    s = iso.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        try:
            dt = datetime.strptime(s[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def session_title(p):
    """customTitle -> aiTitle -> first real user message.

    titleSource is the app's own enum: "user" for a title the user set,
    "auto" otherwise. The app never writes "custom".
    """
    if p["custom_title"]:
        return p["custom_title"], "user"
    if p["ai_title"]:
        return p["ai_title"], "auto"
    if p["first_user_msg"]:
        s = re.sub(r"\s+", " ", p["first_user_msg"]).strip()
        if len(s) > 60:
            s = s[:60].rstrip()
        return s, "auto"
    return "Untitled session", "auto"


# ------------------------------------------------------------------- reference

def build_core(records, threshold):
    """The structural core: fields the app writes for nearly every session.

    The field set is conditional -- a record for a worktree session carries
    worktree fields, one that opened a PR carries pr* fields. Cloning any single
    record therefore stamps its circumstances onto every restored session. Keep
    only what is near-universal, and take values from the most recent record
    that has the field.
    """
    n = len(records)
    presence = {}
    for r in records:
        for k in r:
            presence[k] = presence.get(k, 0) + 1

    keep = [k for k, c in presence.items()
            if c / n >= threshold and k not in NEVER_INHERIT]

    # Most recently active first, so "latest value" means what it says.
    ordered = sorted(records, key=lambda r: r.get("lastActivityAt") or 0, reverse=True)

    core, order = {}, []
    for r in ordered:
        for k in r:
            if k in keep and k not in core:
                core[k] = r[k]
                order.append(k)
    # Preserve the app's own field order from the newest record where possible.
    newest = ordered[0]
    order = [k for k in newest if k in core] + [k for k in order if k not in newest]
    return core, order, presence, n


# ------------------------------------------------------------------------ main

def main():
    ap = argparse.ArgumentParser(
        description="Rebuild the Claude Desktop Code session index from CLI transcripts.",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true",
                    help="Write records. Without it, nothing is written.")
    ap.add_argument("--cwd-prefix", metavar="P",
                    help="Only transcripts whose recorded cwd starts with P.")
    ap.add_argument("--limit", type=int, default=0, metavar="N",
                    help="Only the N most recently active.")
    ap.add_argument("--index-dir", help="Override index auto-detection.")
    ap.add_argument("--account", metavar="UUID",
                    help="Write into this account's folder instead of the one "
                         "with the most records.")
    ap.add_argument("--projects-root", help="Override ~/.claude/projects.")
    ap.add_argument("--include-deleted", action="store_true",
                    help="Re-index tombstoned sessions. Off by default.")
    ap.add_argument("--min-idle-minutes", type=int, default=2, metavar="N",
                    help="Skip transcripts touched in the last N minutes (default 2).")
    ap.add_argument("--core-threshold", type=float, default=0.9, metavar="F",
                    help="Keep fields present in >= this fraction of app-written "
                         "records (default 0.9).")
    ap.add_argument("--no-backup", action="store_true",
                    help="Skip the pre-write backup. Not recommended.")
    args = ap.parse_args()

    step("Locating the Claude Desktop session index")
    index_root = resolve_index_root(args.index_dir)
    acct_dir = resolve_account_dir(index_root, args.account)
    account, org = acct_dir.split(os.sep)[-2:]
    ok("index:   %s" % acct_dir)
    ok("account=%s  org=%s" % (account, org))

    cli_account, _cli_org, app_account = read_account_signals(index_root)
    warn_account_mismatch(account, cli_account, app_account)

    manifest_path = os.path.join(acct_dir, MANIFEST)
    authored = set()
    if os.path.isfile(manifest_path):
        try:
            with open(manifest_path, encoding="utf-8") as f:
                authored = set(json.load(f))
        except Exception:
            pass

    step("Modelling the schema on records the app wrote here")
    existing, app_written = {}, []
    for p in sorted(glob.glob(os.path.join(acct_dir, "local_*.json"))):
        try:
            with open(p, encoding="utf-8-sig") as f:
                r = json.load(f)
        except Exception:
            continue
        if r.get("cliSessionId"):
            existing[r["cliSessionId"]] = os.path.basename(p)
        if r.get("sessionId") not in authored:
            app_written.append(r)

    if not app_written:
        sys.exit(
            "No app-written record to model the schema on.\n\n"
            "Open Claude Desktop, start a Code session in a real folder, send one\n"
            "message, let it finish, quit, then re-run. This tool deliberately\n"
            "refuses to invent a schema: the format is undocumented and the field\n"
            "set varies per session.")

    core, order, presence, n = build_core(app_written, args.core_threshold)
    ok("%d app-written record(s); %d distinct field(s) seen" % (n, len(presence)))
    ok("structural core: %d field(s) present in >=%.0f%% of them"
       % (len(core), args.core_threshold * 100))
    dropped = sorted(k for k in presence if k not in core and k not in DERIVED_FIELDS)
    if dropped:
        print("      conditional/per-session, not inherited: %s" % ", ".join(dropped))
    inherited = [k for k in order if k not in DERIVED_FIELDS and k not in RESET_FIELDS]
    if inherited:
        print("      inheriting verbatim: %s" % ", ".join(inherited))

    # Deleting a session in the UI leaves a tombstone PAIR:
    # deleted_<desktop sessionId> and deleted_<cliSessionId>, same epoch-ms
    # payload. Without honouring these a restore run silently resurrects every
    # session the user ever deleted.
    tombstoned = {os.path.basename(t)[8:]
                  for t in glob.glob(os.path.join(acct_dir, "deleted_*"))}
    if tombstoned:
        ok("%d tombstone file(s) found; deleted sessions will be left alone"
           % len(tombstoned))

    step("Scanning transcripts")
    projects_root = args.projects_root or os.path.join(os.path.expanduser("~"),
                                                       ".claude", "projects")
    if not os.path.isdir(projects_root):
        sys.exit("Transcript root not found: %s" % projects_root)

    # Only <project>/<uuid>.jsonl is a real session. Anything deeper is a
    # subagent transcript or a workflow journal; indexing those floods the picker.
    files = sorted(glob.glob(os.path.join(projects_root, "*", "*.jsonl")))

    records, skips = [], {}
    def skip(reason):
        skips[reason] = skips.get(reason, 0) + 1

    cutoff = time.time() - args.min_idle_minutes * 60
    for path in files:
        base = os.path.basename(path)
        stem = os.path.splitext(base)[0]

        if base.startswith("agent-"):
            skip("subagent transcript (agent-*)"); continue
        if stem in existing:
            skip("already indexed"); continue
        if not args.include_deleted and stem in tombstoned:
            skip("deleted in the app (tombstoned)"); continue
        if args.min_idle_minutes > 0 and os.path.getmtime(path) > cutoff:
            skip("still active (modified < %d min ago)" % args.min_idle_minutes); continue

        p = read_transcript(path, stem)
        if p is None:
            skip("unreadable"); continue
        if not p["has_main_chain"]:
            skip("no main chain (sidechain-only)"); continue
        if not p["cwd"]:
            skip("no cwd recorded"); continue
        if not p["first_ts"]:
            skip("no timestamps"); continue
        if args.cwd_prefix and not p["cwd"].startswith(args.cwd_prefix):
            skip("cwd outside --cwd-prefix"); continue

        title, source = session_title(p)

        rec = {}
        for k in order:
            rec[k] = RESET_FIELDS[k] if k in RESET_FIELDS else core[k]
        rec["sessionId"] = "local_" + str(uuid.uuid4())
        rec["cliSessionId"] = stem
        rec["cwd"] = p["cwd"]
        rec["originCwd"] = origin_of(p["cwd"])
        rec["createdAt"] = to_epoch_ms(p["first_ts"])
        rec["lastActivityAt"] = to_epoch_ms(p["last_ts"])
        rec["lastFocusedAt"] = to_epoch_ms(p["last_ts"])
        rec["title"] = title
        rec["titleSource"] = source
        rec["completedTurns"] = p["turns"]
        records.append(rec)

    records.sort(key=lambda r: r.get("lastActivityAt") or 0, reverse=True)
    if args.limit > 0:
        records = records[:args.limit]

    print()
    print("%-3s %-17s %-46s %s" % ("#", "LAST ACTIVE", "TITLE", "CWD"))
    for i, r in enumerate(records, 1):
        when = datetime.fromtimestamp(r["lastActivityAt"] / 1000).strftime("%Y-%m-%d %H:%M")
        print("%-3d %-17s %-46s %s" % (i, when, r["title"][:44], r["cwd"]))
    print()

    if skips:
        step("Skipped")
        for reason, count in sorted(skips.items(), key=lambda kv: -kv[1]):
            print("  %5d  %s" % (count, reason))
        print()

    if not args.apply:
        warn("DRY RUN -- %d record(s) would be written. Re-run with --apply."
             % len(records))
        return
    if not records:
        ok("Nothing to do."); return

    if not args.no_backup:
        step("Backing up the index")
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = os.path.join(os.path.dirname(index_root),
                              "claude-code-sessions_backup_%s" % stamp)
        shutil.copytree(index_root, backup)
        ok("backup: %s" % backup)
        print("      restore: rm -rf '%s' && cp -R '%s' '%s'"
              % (index_root, backup, index_root))

    step("Writing %d record(s)" % len(records))
    for r in records:
        path = os.path.join(acct_dir, r["sessionId"] + ".json")
        # No BOM, minified, UTF-8, no trailing newline. A BOM makes the app's
        # parser reject the record outright.
        data = json.dumps(r, ensure_ascii=False, separators=(",", ":"))
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write(data)
        authored.add(r["sessionId"])

    with open(manifest_path, "w", encoding="utf-8", newline="") as f:
        f.write(json.dumps(sorted(authored), ensure_ascii=False, separators=(",", ":")))

    ok("Done. Restart Claude Desktop and open the Code session picker.")


if __name__ == "__main__":
    main()
