#!/usr/bin/env python3
"""Zero-install entry point.

    python3 restore_desktop_sessions.py

This exists because of who runs this tool: someone whose session history has
just disappeared. Telling them to set up a virtualenv first is a worse
experience than the bug they are trying to recover from. Clone the repo (or
download this file plus ``src/``) and run it with any Python 3.9+, including
macOS's own ``/usr/bin/python3``.

The installed entry point is equivalent:

    pipx install claude-desktop-session-restore
    restore-desktop-sessions

All the logic lives in ``src/claude_desktop_restore/``. This file only puts that
directory on the path.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

from claude_desktop_restore.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
