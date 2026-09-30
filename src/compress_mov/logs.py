"""Append-only run log at ~/.config/logs/compress.log.

Logging is best-effort: an unwritable log directory or a full disk must
never abort an encode batch, so every write swallows ``OSError``.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

LOG_DIR = Path.home() / ".config" / "logs"
LOG_FILE = LOG_DIR / "compress.log"


def _append(text: str) -> None:
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        with LOG_FILE.open("a") as f:
            f.write(text)
    except OSError:
        pass


def log(message: str) -> None:
    ts = datetime.now().astimezone().strftime("%Y-%m-%dT%H:%M:%S%z")
    _append(f"{ts} {message}\n")


def log_block(text: str) -> None:
    """Append an indented, multi-line detail block (e.g. ffmpeg's stderr)."""
    _append("".join(f"  {line}\n" for line in text.strip().splitlines()))
