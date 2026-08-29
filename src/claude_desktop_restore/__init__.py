"""Rebuild the Claude Desktop Code session picker from CLI transcripts.

The app writes a small pointer record per session at
``<index-root>/<accountUuid>/<orgUuid>/local_<uuid>.json``, linking to a CLI
transcript through ``cliSessionId``. It only writes records for sessions it
creates itself, so CLI sessions -- and any ``.claude`` tree carried over from
another machine -- are invisible in the picker while every transcript sits
intact on disk.

This package regenerates the missing records. See SCHEMA.md for the format and
the evidence behind every claim it relies on.
"""

from __future__ import annotations

__version__ = "0.2.0"

__all__ = ["__version__"]
