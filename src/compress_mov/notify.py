"""macOS notification banners, via osascript."""

from __future__ import annotations

import contextlib
import subprocess

from .bins import OSASCRIPT

# The title and message travel as argv items, not interpolated into the
# script source, so quotes, backslashes and newlines in them can't break
# (or inject into) the AppleScript.
_SCRIPT = (
    "on run argv",
    "display notification (item 2 of argv) with title (item 1 of argv)",
    "end run",
)


def notify(title: str, message: str) -> None:
    """Show a notification banner. Best-effort: never raises."""
    cmd = [OSASCRIPT]
    for line in _SCRIPT:
        cmd += ["-e", line]
    cmd += [title, message]
    # OSError: osascript missing (non-macOS) or not executable.
    with contextlib.suppress(OSError):
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
