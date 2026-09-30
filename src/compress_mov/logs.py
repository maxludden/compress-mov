"""Append-only run log at ~/.config/logs/compress.log."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

LOG_DIR = Path.home() / ".config" / "logs"
LOG_FILE = LOG_DIR / "compress.log"


def log(message: str) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().astimezone().strftime("%Y-%m-%dT%H:%M:%S%z")
    with LOG_FILE.open("a") as f:
        f.write(f"{ts} {message}\n")
