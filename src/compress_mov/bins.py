"""Absolute paths to external binaries.

A Finder Quick Action runs with a minimal ``$PATH`` that doesn't include
Homebrew, so every external tool is invoked by absolute path rather than
relying on lookup. ffmpeg/ffprobe are located once at import: ``$PATH``
first, then the Homebrew prefixes for Apple Silicon and Intel Macs.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

HOMEBREW_DIRS = ("/opt/homebrew/bin", "/usr/local/bin")


def find_binary(name: str, search_dirs: tuple[str, ...] = HOMEBREW_DIRS) -> str | None:
    """Absolute path to `name`, or None if it can't be found.

    Checks ``$PATH`` first, then `search_dirs` (for the minimal ``$PATH`` a
    Quick Action gets).
    """
    on_path = shutil.which(name)
    if on_path:
        return on_path
    for directory in search_dirs:
        candidate = Path(directory) / name
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


# When not found, fall back to the Apple Silicon Homebrew path so the error
# message names a concrete location; `missing_tools()` reports it before use.
FFMPEG = find_binary("ffmpeg") or f"{HOMEBREW_DIRS[0]}/ffmpeg"
FFPROBE = find_binary("ffprobe") or f"{HOMEBREW_DIRS[0]}/ffprobe"
OPEN = "/usr/bin/open"
OSASCRIPT = "/usr/bin/osascript"
CAFFEINATE = "/usr/bin/caffeinate"


def missing_tools() -> list[str]:
    """Names of the required encoder binaries that aren't executable."""
    required = {"ffmpeg": FFMPEG, "ffprobe": FFPROBE}
    return [name for name, path in required.items() if not (os.path.isfile(path) and os.access(path, os.X_OK))]
