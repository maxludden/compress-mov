"""Sequential batch execution for the TUI (no Textual import).

Mirrors the CLI's loop (temp dir, sleep prevention, one file at a time)
without its signal handlers, ``sys.exit`` or console output, so a front end
that owns the terminal can drive it from a worker thread.
"""

from __future__ import annotations

import shutil
import tempfile
import threading
from collections.abc import Callable
from pathlib import Path

from ..encode import EncodeResult, ProgressLike, encode_one
from ..formatting import delta_phrase, human, saved_pct
from ..power import caffeinate

OnStart = Callable[[int, int, Path], None]
# (index, total, video, result, cancelled)
OnResult = Callable[[int, int, Path, EncodeResult, bool], None]


def run_batch(
    videos: list[Path],
    *,
    keep_larger: bool,
    progress: ProgressLike,
    stop: threading.Event,
    on_start: OnStart,
    on_result: OnResult,
) -> list[EncodeResult]:
    """Encode `videos` in order, stopping before the next file once `stop` is set.

    Returns the results for the files that ran.
    """
    total = len(videos)
    results: list[EncodeResult] = []
    work_dir = Path(tempfile.mkdtemp(prefix="compress-vid."))
    try:
        with caffeinate():
            for i, video in enumerate(videos, start=1):
                if stop.is_set():
                    break
                on_start(i, total, video)
                result = encode_one(i, total, video, work_dir, progress, keep_larger=keep_larger)
                results.append(result)
                on_result(i, total, video, result, stop.is_set() and not result.ok)
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)
    return results


def describe_result(result: EncodeResult, cancelled: bool = False) -> tuple[str, str]:
    """(status, detail) text for a finished file."""
    if cancelled:
        return "cancelled", ""
    if result.discarded:
        return "skipped", f"no savings ({human(result.in_bytes)} → {human(result.out_bytes)}); original kept"
    if result.ok:
        return "done", f"{human(result.out_bytes)} ({delta_phrase(saved_pct(result.in_bytes, result.out_bytes))})"
    return "failed", result.error or f"exit {result.rc}"


def summarize(results: list[EncodeResult], cancelled: bool = False) -> str:
    done = [r for r in results if r.ok and not r.discarded]
    skipped = [r for r in results if r.discarded]
    failed = [r for r in results if not r.ok]
    parts = []
    if done:
        bytes_in, bytes_out = sum(r.in_bytes for r in done), sum(r.out_bytes for r in done)
        parts.append(
            f"{len(done)} compressed: {human(bytes_in)} → {human(bytes_out)} "
            f"({delta_phrase(saved_pct(bytes_in, bytes_out))})"
        )
    if skipped:
        parts.append(f"{len(skipped)} skipped (no savings)")
    if failed:
        parts.append(f"{len(failed)} {'stopped or failed' if cancelled else 'failed'}")
    text = ", ".join(parts) or "nothing was encoded"
    return f"Stopped early: {text}" if cancelled else f"Finished: {text}"
