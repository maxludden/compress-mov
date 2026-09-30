"""ffprobe duration lookup and the ffmpeg HEVC encode itself."""

from __future__ import annotations

import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from rich.progress import Progress, TaskID

from . import logs
from .bins import FFMPEG, FFPROBE
from .formatting import clock, human
from .streams import StreamPlan, plan_streams, probe_streams
from .ui import console


@dataclass
class EncodeResult:
    ok: bool
    in_bytes: int
    out_bytes: int
    elapsed: float
    rc: int


class _Current:
    """The in-flight ffmpeg process, tracked so a signal can clean it up."""

    proc: subprocess.Popen | None = None
    out_path: Path | None = None


_CURRENT = _Current()


def handle_interrupt(signum: int, frame: object) -> None:
    """Kill the in-flight encode and drop its half-written output.

    Reap before unlinking: exiting without waiting can leave ffmpeg
    running detached, still writing into a file whose path we already
    removed.
    """
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

    d = _run([FFPROBE, "-v", "error", "-show_entries", "format=duration", "-of", "default=nk=1:nw=1", str(path)])
    if d <= 0:
        d = _run(
            [
                FFPROBE, "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=duration",
                "-of", "default=nk=1:nw=1", str(path),
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
    err_text = err_file.read_text().strip()
    if err_text:
        with logs.LOG_FILE.open("a") as f:
            f.write(f"  ffmpeg: {err_text}\n")


def _run_ffmpeg(
    cmd: list[str], out_path: Path, err_file: Path, progress: Progress, task_id: TaskID, task_total: int | None, label: str
) -> int:
    """Run one ffmpeg attempt, feeding its -progress output to `progress`."""
    # ffmpeg's stderr goes to a file, not a pipe: if warnings ever exceed the
    # OS pipe buffer before ffmpeg exits, a pipe would deadlock -- ffmpeg
    # blocked writing stderr while we're blocked waiting for stdout EOF.
    with err_file.open("w") as err_fh:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=err_fh, text=True, bufsize=1)
        _CURRENT.proc = proc
        _CURRENT.out_path = out_path

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


def encode_one(index: int, total: int, in_path: Path, work_dir: Path, progress: Progress) -> EncodeResult:
    """Encode one file to HEVC .mp4 next to the original, updating `progress`."""
    out_path = in_path.with_name(f"{in_path.stem} (HEVC).mp4")
    if out_path.exists():
        # Avoid overwriting if the batch (or a past run) already produced one.
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        out_path = in_path.with_name(f"{in_path.stem} (HEVC)-{stamp}.mp4")

    duration = probe_duration(in_path)
    in_bytes = in_path.stat().st_size
    label = f"[{index}/{total}] {in_path.name}"

    logs.log(f'START in="{in_path}" out="{out_path}" size="{human(in_bytes)}" duration="{duration}s"')

    err_file = work_dir / f"stderr.{index}"
    task_total = round(duration) if duration > 0 else None
    task_id = progress.add_task(label, total=task_total)

    streams = probe_streams(in_path)
    plan = plan_streams(streams)
    reported: set[str] = set()
    _report_plan(plan, in_path, progress, reported)

    start = time.monotonic()
    rc = _run_ffmpeg(_build_cmd(in_path, out_path, plan), out_path, err_file, progress, task_id, task_total, label)

    if rc != 0 and plan.extras:
        # The container rejected a best-effort stream (ffmpeg reports that
        # when writing the header). Retry once without them rather than
        # failing the whole file over a cover image or metadata track.
        logs.log(f'RETRY in="{in_path}" rc={rc} without: {", ".join(plan.extras)}')
        _log_ffmpeg_stderr(err_file)
        if out_path.exists():
            out_path.unlink()
        plan = plan_streams(streams, keep_extras=False)
        _report_plan(plan, in_path, progress, reported)
        rc = _run_ffmpeg(_build_cmd(in_path, out_path, plan), out_path, err_file, progress, task_id, task_total, label)

    elapsed = time.monotonic() - start
    progress.remove_task(task_id)

    if rc != 0:
        logs.log(f'FAIL in="{in_path}" out="{out_path}" rc={rc}')
        _log_ffmpeg_stderr(err_file)
        if out_path.exists():
            out_path.unlink()
        _CURRENT.out_path = None
        return EncodeResult(False, in_bytes, 0, elapsed, rc)

    out_bytes = out_path.stat().st_size
    saved = 100.0 * (in_bytes - out_bytes) / in_bytes
    logs.log(
        f'DONE in="{in_path}" out="{out_path}" in_bytes={in_bytes} out_bytes={out_bytes} '
        f'saved="{saved:.1f}%" elapsed="{clock(elapsed)}"'
    )
    _CURRENT.out_path = None
    return EncodeResult(True, in_bytes, out_bytes, elapsed, 0)
