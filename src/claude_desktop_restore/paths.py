"""Finding the session index, and working out which account owns what.

The index is partitioned by account, and the picker only reads the folder for
the account the app is signed in as. That is invisible from inside the app and
is the cause of two symptoms that look like bugs -- see ``AccountSignals``.
"""

from __future__ import annotations

import glob
import json
import os
import platform
from dataclasses import dataclass

INDEX_DIRNAME = "claude-code-sessions"


class IndexNotFound(Exception):
    """No index directory on this machine. Carries the probed paths."""

    def __init__(self, probed: list[str]):
        self.probed = probed
        super().__init__(
            "No Claude Desktop session index found. Probed:\n  "
            + "\n  ".join(probed)
            + "\n\nIf the app has never run here, open it, start one Code session,"
            "\nsend a message, let it finish, then re-run. This tool needs"
            "\napp-written records to model the schema on."
        )


def candidate_index_roots() -> list[str]:
    """Every plausible index location, most specific first.

    The Windows Store build is packaged, so its ``%APPDATA%`` writes are
    redirected into the package container. Probing plain ``%APPDATA%`` on a
    Store install finds nothing and suggests the app has never run, so the MSIX
    path must come first.
    """
    home = os.path.expanduser("~")
    candidates: list[str] = []

    local_appdata = os.environ.get("LOCALAPPDATA")
    if local_appdata:
        pattern = os.path.join(local_appdata, "Packages", "Claude_*")
        for pkg in sorted(glob.glob(pattern)):
            candidates.append(
                os.path.join(pkg, "LocalCache", "Roaming", "Claude", INDEX_DIRNAME)
            )

    appdata = os.environ.get("APPDATA")
    if appdata:
        candidates.append(os.path.join(appdata, "Claude", INDEX_DIRNAME))

    if platform.system() == "Darwin":
        candidates.append(
            os.path.join(home, "Library", "Application Support", "Claude", INDEX_DIRNAME)
        )

    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        candidates.append(os.path.join(xdg, "Claude", INDEX_DIRNAME))

    candidates.append(os.path.join(home, ".config", "Claude", INDEX_DIRNAME))
    candidates.append(
        os.path.join(home, "Library", "Application Support", "Claude", INDEX_DIRNAME)
    )

    seen, unique = set(), []
    for path in candidates:
        if path not in seen:
            seen.add(path)
            unique.append(path)
    return unique


def resolve_index_root(override: str | None = None) -> str:
    if override:
        if not os.path.isdir(override):
            raise IndexNotFound([override])
        return os.path.abspath(override)
    candidates = candidate_index_roots()
    for path in candidates:
        if os.path.isdir(path):
            return path
    raise IndexNotFound(candidates)


@dataclass
class AccountDir:
    path: str
    account: str
    org: str
    records: int


def list_account_dirs(index_root: str) -> list[AccountDir]:
    """Every ``<accountUuid>/<orgUuid>`` pair under the index root.

    Account first, then org. Confirmed three ways on macOS: ``~/.claude.json``
    ``oauthAccount``, ``config.json`` ``lastKnownAccountUuid``, and the app's own
    telemetry blobs. See SCHEMA.md -- an earlier revision of this project had it
    backwards.
    """
    dirs: list[AccountDir] = []
    for path in sorted(glob.glob(os.path.join(index_root, "*", "*"))):
        if not os.path.isdir(path):
            continue
        account, org = path.split(os.sep)[-2:]
        count = len(glob.glob(os.path.join(path, "local_*.json")))
        dirs.append(AccountDir(path=path, account=account, org=org, records=count))
    return dirs


@dataclass
class AccountSignals:
    """Who authored the transcripts, and who the app is showing.

    Two different questions, two different files, and they can disagree:

    - ``cli_account`` (``~/.claude.json`` -> ``oauthAccount.accountUuid``) is the
      account the CLI is signed in as, so it owns the transcripts and anything
      those sessions published server-side, artifacts included.
    - ``app_account`` (``config.json`` -> ``lastKnownAccountUuid``) is the account
      the Desktop app is showing. The picker reads only that account's folder.
    """

    cli_account: str | None = None
    cli_org: str | None = None
    app_account: str | None = None


def read_account_signals(index_root: str) -> AccountSignals:
    signals = AccountSignals()
    try:
        path = os.path.join(os.path.expanduser("~"), ".claude.json")
        with open(path, encoding="utf-8-sig") as f:
            oauth = json.load(f).get("oauthAccount") or {}
        signals.cli_account = oauth.get("accountUuid")
        signals.cli_org = oauth.get("organizationUuid")
    except Exception:
        pass
    try:
        path = os.path.join(os.path.dirname(index_root), "config.json")
        with open(path, encoding="utf-8-sig") as f:
            signals.app_account = json.load(f).get("lastKnownAccountUuid")
    except Exception:
        pass
    return signals
