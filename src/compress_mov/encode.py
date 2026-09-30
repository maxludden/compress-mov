"""ffprobe duration lookup and the ffmpeg HEVC encode itself."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import IO

from rich.progress import Progress, TaskID

from . import logs
from .bins import FFMPEG, FFPROBE
from .formatting import clock, human, saved_pct
from .streams import StreamPlan, plan_streams, probe_streams
from .ui import console


@dataclass
class EncodeResult:
    ok: bool
    in_bytes: int
    out_bytes: int
    elapsed: float
    rc: int
    # The encode worked but the output wasn't smaller, so it was deleted.
    discarded: bool = False
    # Set when an unexpected exception (not an ffmpeg exit code) failed the file.
    error: str | None = None


class _Current:
    """The in-flight ffmpeg process, tracked so a signal can clean it up.

    `starting` covers the window between forking ffmpeg and recording its
    handle: a signal arriving then can't kill a process we don't have a
    reference to yet, so it's parked in `pending` and replayed once the
    handle is recorded (see :func:`_spawn`).
    """

    proc: subprocess.Popen[str] | None = None
    out_path: Path | None = None
    starting: bool = False
    pending: int | None = None


_CURRENT = _Current()


def handle_interrupt(signum: int, frame: object) -> None:
    """Kill the in-flight encode and drop its half-written output.

    Reap before unlinking: exiting without waiting can leave ffmpeg
    running detached, still writing into a file whose path we already
    removed.
    """
    if _CURRENT.starting:
        _CURRENT.pending = signum
        return
    if _CURRENT.proc is not None:
        _CURRENT.proc.terminate()
        try:
            _CURRENT.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _CURRENT.proc.kill()
    if _CURRENT.out_path is not None and _CURRENT.out_path.exists():
        _CURRENT.out_path.unlink()
    console.print("compress-mov: interrupted")
    sys.exit(130)


def _spawn(cmd: list[str], err_fh: IO[str], out_path: Path) -> subprocess.Popen[str]:
    """Start ffmpeg and register it for signal cleanup without a race.

    The output path is recorded first, and a signal that lands while
    Popen is still returning is deferred and replayed here, once the
    process handle exists, so it terminates ffmpeg and removes the file.
    """
    _CURRENT.out_path = out_path
    _CURRENT.pending = None
    _CURRENT.starting = True
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=err_fh, text=True, bufsize=1)
        _CURRENT.proc = proc
    finally:
        _CURRENT.starting = False
        pending, _CURRENT.pending = _CURRENT.pending, None
        if pending is not None:
            handle_interrupt(pending, None)
    return proc


def _abort_current() -> None:
    """Best-effort cleanup after an unexpected exception mid-encode."""
    proc, out_path = _CURRENT.proc, _CURRENT.out_path
    _CURRENT.proc = _CURRENT.out_path = None
    if proc is not None and proc.poll() is None:
        proc.kill()
        proc.wait()
    if out_path is not None:
        out_path.unlink(missing_ok=True)


def reserve_output_path(in_path: Path) -> Path:
    """Atomically claim ``<stem> (HEVC).mp4`` next to the input, or the first free ``... (HEVC) N.mp4``.

    The file is created here with ``O_EXCL``, so the name is ours even if
    another process is picking outputs at the same moment, and every later
    cleanup (failure, discard, interrupt) only ever deletes a file this run
    created. ffmpeg then overwrites the empty placeholder (``-y``). A counter,
    not a timestamp, so any number of collisions get distinct names.
    """
    n = 1
    while True:
        name = f"{in_path.stem} (HEVC).mp4" if n == 1 else f"{in_path.stem} (HEVC) {n}.mp4"
        candidate = in_path.with_name(name)
        try:
            fd = os.open(candidate, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o666)
        except FileExistsError:
            n += 1
            continue
        os.close(fd)
        return candidate


def _copy_times(src: Path, dst: Path) -> None:
    """Give the output the source's access/modification times.

    Keeps Finder sort order and Photos import dates. On macOS, setting an
    mtime earlier than the birth time also pulls the birth time back to it.
    """
    try:
        st = src.stat()
        os.utime(dst, ns=(st.st_atime_ns, st.st_mtime_ns))
    except OSError:
        pass


def probe_duration(path: Path) -> float:
    """Total duration in seconds, or 0.0 if it can't be determined.

    Falls back to the video stream's duration when the container header
    doesn't carry one.
    """

    def _run(args: list[str]) -> float:
        out = subprocess.run(args, capture_output=True, text=True, check=False).stdout.strip()
        try:
            return float(out)
        except ValueError:
            return 0.0

    d = _run([FFPROBE, "-v", "error", "-show_entries", "format=duration", "-of", "default=nk=1:nw=1", "-i", str(path)])
    if d <= 0:
        d = _run(
            [
                FFPROBE,
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=duration",
                "-of",
                "default=nk=1:nw=1",
                "-i",
                str(path),
            ]
        )
    return d


def _build_cmd(in_path: Path, out_path: Path, plan: StreamPlan) -> list[str]:
    return [
        FFMPEG, "-hide_banner", "-loglevel", "warning", "-y",
        "-i", str(in_path),
        *plan.args,
        "-c:v", "libx265", "-preset", "slow", "-crf", "28", "-x265-params", "log-level=error",
        "-movflags", "+faststart",
        "-progress", "pipe:1", "-nostats",
        str(out_path),
    ]  # fmt: skip


def _report_plan(plan: StreamPlan, in_path: Path, progress: Progress, reported: set[str]) -> None:
    """Log/print the plan's losses, skipping any already reported for this file."""
    for warning in plan.warnings:
        if warning not in reported:
            reported.add(warning)
            logs.log(f'WARN in="{in_path}" {warning}')
            progress.console.print(f"  ! {in_path.name}: {warning}", style="yellow")
    for note in plan.dropped:
        logs.log(f'DROP in="{in_path}" {note}')


def _log_ffmpeg_stderr(err_file: Path) -> None:
    try:
        err_text = err_file.read_text().strip()
    except OSError:
        return
    if err_text:
        logs.log_block(f"ffmpeg: {err_text}")


def _run_ffmpeg(
    cmd: list[str],
    out_path: Path,
    err_file: Path,
    progress: Progress,
    task_id: TaskID,
    task_total: int | None,
    label: str,
) -> int:
    """Run one ffmpeg attempt, feeding its -progress output to `progress`."""
    # ffmpeg's stderr goes to a file, not a pipe: if warnings ever exceed the
    # OS pipe buffer before ffmpeg exits, a pipe would deadlock -- ffmpeg
    # blocked writing stderr while we're blocked waiting for stdout EOF.
    with err_file.open("w") as err_fh:
        proc = _spawn(cmd, err_fh, out_path)

        speed = ""
        out_secs = 0.0
        assert proc.stdout is not None
        for line in proc.stdout:
            line = line.strip()
            if line.startswith("out_time_us="):
                raw = line.removeprefix("out_time_us=")
                if raw.lstrip("-").isdigit():
                    out_secs = max(0, int(raw)) / 1_000_000
            elif line.startswith("speed="):
                speed = line.removeprefix("speed=").removesuffix("x")

            suffix = f"  {speed}x" if speed else ""
            if task_total is not None:
                progress.update(task_id, completed=min(out_secs, task_total), description=label + suffix)
            else:
                progress.update(task_id, description=f"{label}  {clock(out_secs)} elapsed{suffix}")

        rc = proc.wait()

    _CURRENT.proc = None
    return rc


def encode_one(
    index: int, total: int, in_path: Path, work_dir: Path, progress: Progress, keep_larger: bool = False
) -> EncodeResult:
    """Encode one file to HEVC .mp4 next to the original, updating `progress`.

    Never raises for an ordinary failure (unreadable file, full disk, ffmpeg
    won't start...): one bad file is recorded as a failed result so the rest
    of the batch still runs. ``SystemExit`` from the signal handler is not
    an ``Exception`` and passes straight through.
    """
    try:
        return _encode_one(index, total, in_path, work_dir, progress, keep_larger)
    except Exception as exc:
        _abort_current()
        message = f"{type(exc).__name__}: {exc}"
        logs.log(f'ERROR in="{in_path}" {message}')
        try:
            in_bytes = in_path.stat().st_size
        except OSError:
            in_bytes = 0
        return EncodeResult(False, in_bytes, 0, 0.0, -1, error=message)


def _encode_one(
    index: int, total: int, in_path: Path, work_dir: Path, progress: Progress, keep_larger: bool
) -> EncodeResult:
    duration = probe_duration(in_path)
    in_bytes = in_path.stat().st_size
    out_path = reserve_output_path(in_path)
    # Registered immediately so any exit path (error, signal) removes the
    # placeholder we just created, and only that.
    _CURRENT.out_path = out_path
    label = f"[{index}/{total}] {in_path.name}"

    logs.log(f'START in="{in_path}" out="{out_path}" size="{human(in_bytes)}" duration="{duration}s"')

    err_file = work_dir / f"stderr.{index}"
    task_total = round(duration) if duration > 0 else None
    task_id = progress.add_task(label, total=task_total)
    start = time.monotonic()
    try:
        streams = probe_streams(in_path)
        plan = plan_streams(streams)
        reported: set[str] = set()
        _report_plan(plan, in_path, progress, reported)

        rc = _run_ffmpeg(_build_cmd(in_path, out_path, plan), out_path, err_file, progress, task_id, task_total, label)

        if rc != 0 and plan.extras:
            # The container rejected a best-effort stream (ffmpeg reports that
            # when writing the header). Retry once without them rather than
            # failing the whole file over a cover image or metadata track.
            logs.log(f'RETRY in="{in_path}" rc={rc} without: {", ".join(plan.extras)}')
            _log_ffmpeg_stderr(err_file)
            # No unlink: the reserved file is ours and the retry's -y overwrites it.
            plan = plan_streams(streams, keep_extras=False)
            _report_plan(plan, in_path, progress, reported)
            rc = _run_ffmpeg(
                _build_cmd(in_path, out_path, plan), out_path, err_file, progress, task_id, task_total, label
            )
    finally:
        progress.remove_task(task_id)
    elapsed = time.monotonic() - start

    if rc != 0:
        logs.log(f'FAIL in="{in_path}" out="{out_path}" rc={rc}')
        _log_ffmpeg_stderr(err_file)
        out_path.unlink(missing_ok=True)
        _CURRENT.out_path = None
        return EncodeResult(False, in_bytes, 0, elapsed, rc)

    out_bytes = out_path.stat().st_size
    if out_bytes >= in_bytes and not keep_larger:
        # Re-encoding didn't help; the original is untouched, so the bigger
        # file would only be clutter.
        out_path.unlink(missing_ok=True)
        _CURRENT.out_path = None
        logs.log(
            f'SKIP in="{in_path}" in_bytes={in_bytes} out_bytes={out_bytes} reason="output not smaller; discarded"'
        )
        return EncodeResult(True, in_bytes, out_bytes, elapsed, 0, discarded=True)

    _copy_times(in_path, out_path)
    logs.log(
        f'DONE in="{in_path}" out="{out_path}" in_bytes={in_bytes} out_bytes={out_bytes} '
        f'saved="{saved_pct(in_bytes, out_bytes):.1f}%" elapsed="{clock(elapsed)}"'
    )
    _CURRENT.out_path = None
    return EncodeResult(True, in_bytes, out_bytes, elapsed, 0)
