"""macOS notification banners, via osascript."""

from __future__ import annotations

import subprocess

from .bins import OSASCRIPT


def notify(title: str, message: str) -> None:
    subprocess.run(
        [OSASCRIPT, "-e", f'display notification "{message}" with title "{title}"'],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
