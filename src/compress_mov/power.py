"""Keep macOS from idle-sleeping mid-batch."""

from __future__ import annotations

import os
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager

from .bins import CAFFEINATE


@contextmanager
def caffeinate() -> Iterator[None]:
    """Hold a `caffeinate -i` assertion for the lifetime of the `with` block.

    Tied to our own pid (`-w`) as a safety net: if we're killed outright,
    caffeinate exits with us instead of holding the assertion forever.
    """
    proc: subprocess.Popen[bytes] | None = None
    try:
        proc = subprocess.Popen([CAFFEINATE, "-i", "-w", str(os.getpid())])
    except FileNotFoundError:
        proc = None
    try:
        yield
    finally:
        if proc is not None:
            proc.terminate()
