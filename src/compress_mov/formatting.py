"""Pure display-formatting helpers shared by the log and the progress UI."""

from __future__ import annotations


def human(nbytes: int) -> str:
    """Render a byte count the way `ls -lh` roughly would: GB/MB/KB/B."""
    if nbytes >= 1024**3:
        return f"{nbytes / 1024**3:.2f} GB"
    if nbytes >= 1024**2:
        return f"{nbytes / 1024**2:.1f} MB"
    if nbytes >= 1024:
        return f"{nbytes / 1024:.1f} KB"
    return f"{nbytes} B"


def delta_phrase(pct: float) -> str:
    """Signed-but-readable size delta; re-encoding can grow a file."""
    return f"{pct:.1f}% smaller" if pct >= 0 else f"{-pct:.1f}% larger"


def clock(seconds: float) -> str:
    """Seconds -> M:SS, or H:MM:SS once past an hour."""
    t = max(0, int(seconds))
    if t >= 3600:
        return f"{t // 3600}:{(t % 3600) // 60:02d}:{t % 60:02d}"
    return f"{t // 60}:{t % 60:02d}"
