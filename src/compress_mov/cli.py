"""Typer CLI: argument parsing and batch orchestration."""

from __future__ import annotations

import os
import shutil
import signal
import sys
import tempfile
from pathlib import Path

import typer
from rich.progress import BarColumn, Progress, TextColumn, TimeRemainingColumn

from .discovery import resolve_inputs
from .encode import EncodeResult, encode_one, handle_interrupt
from .formatting import clock, delta_phrase, human
from .logs import LOG_FILE
from .notify import notify
from .power import caffeinate
from .terminal import relaunch_in_terminal
from .ui import console

app = typer.Typer(add_completion=False)


@app.command()
def main(
    paths: list[Path] = typer.Argument(..., metavar="FILE|DIR", help="A .mov file or a directory containing them"),
    recursive: bool = typer.Option(
        False, "-r", "--recursive", help="Include .mov files in subdirectories of each directory"
    ),
) -> None:
    """Compress .mov file(s) to HEVC .mp4, saved next to the originals."""
    signal.signal(signal.SIGINT, handle_interrupt)
    signal.signal(signal.SIGTERM, handle_interrupt)

    videos = resolve_inputs(paths, recursive)
    if not videos:
        console.print("compress-mov: no .mov files found", style="red")
        raise typer.Exit(1)

    total = len(videos)
    console.print(f"[bold]▸[/bold] Compressing {total} video(s) to HEVC .mp4 (saved next to the originals)…")

    work_dir = Path(tempfile.mkdtemp(prefix="compress-vid."))
    results: list[EncodeResult] = []
    try:
        with caffeinate():
            columns = [
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
                TimeRemainingColumn(),
            ]
            with Progress(*columns, console=console, transient=True) as progress:
                for i, video in enumerate(videos, start=1):
                    result = encode_one(i, total, video, work_dir, progress)
                    results.append(result)
                    if result.ok:
                        pct = 100.0 * (result.in_bytes - result.out_bytes) / result.in_bytes
                        console.print(
                            f"[{i}/{total}] {video.name}  ✓ {human(result.in_bytes)} → {human(result.out_bytes)} "
                            f"({delta_phrase(pct)}) in {clock(result.elapsed)}"
                        )
                    else:
                        console.print(
                            f"[{i}/{total}] {video.name}  ✗ failed (exit {result.rc}) — see {LOG_FILE}",
                            style="red",
                        )
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)

    done = [r for r in results if r.ok]
    failed = [r for r in results if not r.ok]

    # Batch summary, only when it isn't just restating the single-file line above.
    if total > 1:
        if done:
            bytes_in = sum(r.in_bytes for r in done)
            bytes_out = sum(r.out_bytes for r in done)
            overall = 100.0 * (bytes_in - bytes_out) / bytes_in
            summary = f"{len(done)}/{total} compressed: {human(bytes_in)} → {human(bytes_out)} ({delta_phrase(overall)})"
        else:
            summary = f"0/{total} compressed"
        if failed:
            summary += f", {len(failed)} failed"
        console.print(summary)

    if os.environ.get("COMPRESS_MOV_LAUNCHED"):
        msg = f"Finished compressing {len(done)} video(s)" if not failed else "Finished with errors — see the Terminal window"
        notify("compress-mov", msg)

    raise typer.Exit(1 if failed else 0)


def run() -> None:
    """Console-script entry point.

    Handles the Finder Quick Action relaunch-into-Terminal step *before*
    Typer/Click touches argv, so a Quick Action with zero usable input
    still gets a visible window to report that in.
    """
    if len(sys.argv) < 2:
        print("usage: compress-mov [-r] <file.mov | directory> [...]", file=sys.stderr)
        raise SystemExit(2)

    if not sys.stderr.isatty() and "COMPRESS_MOV_LAUNCHED" not in os.environ:
        relaunch_in_terminal(sys.argv[1:], self_path=Path(sys.argv[0]).resolve())
        raise SystemExit(0)

    app()
