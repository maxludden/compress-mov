"""The Textual app. The only module in the package that imports Textual."""

from __future__ import annotations

import contextlib
import shlex
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar

from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal
from textual.widgets import Button, DataTable, Footer, Header, Input, Label, ProgressBar, RichLog, Static, Switch

from ..bins import missing_tools
from ..discovery import resolve_inputs
from ..encode import EncodeResult, cancel_current
from ..formatting import human
from .args import TuiArgs
from .progress import ProgressAdapter
from .runner import describe_result, run_batch, summarize

COLUMNS = ("#", "File", "Size", "Status", "Result")
KILL_RETRY_SECONDS = 0.2


@dataclass
class BatchState:
    videos: list[Path] = field(default_factory=list)
    status: dict[Path, str] = field(default_factory=dict)
    seen: set[Path] = field(default_factory=set)
    running: bool = False
    quit_requested: bool = False
    failed: int = 0
    stop: threading.Event = field(default_factory=threading.Event)


class CompressApp(App[None]):
    TITLE = "compress-mov"
    SUB_TITLE = "HEVC batch compressor"

    CSS = """
    #controls { height: 3; padding: 0 1; }
    #path { width: 1fr; }
    #controls Label { padding: 1 1 0 2; }
    #queue { height: 1fr; min-height: 5; }
    #current { height: 1; padding: 0 1; }
    #bar { padding: 0 1; height: 1; }
    #log { height: 8; border-top: solid $panel; }
    #buttons { height: 3; padding: 0 1; }
    Button { margin-right: 2; }
    """

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("a", "add", "Add path"),
        Binding("s", "start", "Start"),
        Binding("x", "clear", "Clear queue"),
        Binding("q", "quit", "Quit"),
    ]

    def __init__(self, args: TuiArgs | None = None) -> None:
        super().__init__()
        self._args = args or TuiArgs()
        # All mutable state lives in one object: Textual's App has many private
        # attributes (e.g. ``_running``) and overriding one breaks the framework.
        self.batch = BatchState()

    # -- layout ---------------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="controls"):
            yield Input(placeholder="Add a .mov file or folder, then press Enter", id="path")
            yield Label("Recursive")
            yield Switch(value=self._args.recursive, id="recursive")
            yield Label("Keep larger")
            yield Switch(value=self._args.keep_larger, id="keep-larger")
        yield DataTable(id="queue", cursor_type="row", zebra_stripes=True)
        yield Static("", id="current")
        yield ProgressBar(total=100, show_eta=True, id="bar")
        yield RichLog(id="log", wrap=True, markup=False, highlight=False)
        with Horizontal(id="buttons"):
            yield Button("Start", id="start", variant="success")
            yield Button("Clear queue", id="clear")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#queue", DataTable)
        for name in COLUMNS:
            table.add_column(name, key=name)
        table.focus()  # so the single-key bindings work instead of typing into the input

        missing = missing_tools()
        if missing:
            self.write_log(f"{' and '.join(missing)} not found — install with `brew install ffmpeg`", "red")
            self.query_one("#start", Button).disabled = True
        else:
            self.write_log("Add files or folders with 'a', then press 's' to start.")
        if self._args.paths:
            self._scan(list(self._args.paths), self._args.recursive)

    # -- helpers --------------------------------------------------------------

    def write_log(self, message: str, style: str | None = None) -> None:
        self.query_one("#log", RichLog).write(Text(message, style=style or ""))

    def _ui(self, fn: Callable[..., Any], *args: Any) -> None:
        """Run `fn` on the UI thread from a worker; a no-op once the app is gone."""
        with contextlib.suppress(Exception):  # the app may already be gone mid-batch
            self.call_from_thread(fn, *args)

    def _set_cell(self, video: Path, column: str, value: str) -> None:
        self.query_one("#queue", DataTable).update_cell(str(video), column, value)

    def _set_status(self, video: Path, status: str, detail: str = "") -> None:
        self.batch.status[video] = status
        self._set_cell(video, "Status", status)
        self._set_cell(video, "Result", detail)

    # -- adding files ---------------------------------------------------------

    def action_add(self) -> None:
        self.query_one("#path", Input).focus()

    @on(Input.Submitted, "#path")
    def _path_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        event.input.value = ""
        self.query_one("#queue", DataTable).focus()
        if not text:
            return
        try:
            # shlex handles quoted paths and the backslash-escaped spaces a
            # terminal produces when a file is dragged onto it.
            parts = shlex.split(text)
        except ValueError as exc:
            self.write_log(f"couldn't parse that path: {exc}", "red")
            return
        self._scan([Path(p).expanduser() for p in parts], self.query_one("#recursive", Switch).value)

    @work(thread=True, group="scan")
    def _scan(self, paths: list[Path], recursive: bool) -> None:
        def say(message: str) -> None:
            self._ui(self.write_log, message.replace("[bold]", "").replace("[/bold]", ""))

        videos = resolve_inputs(paths, recursive, say)
        self._ui(self._enqueue, videos)

    def _enqueue(self, videos: list[Path]) -> None:
        table = self.query_one("#queue", DataTable)
        added = 0
        for video in videos:
            real = video.resolve()
            if real in self.batch.seen:
                continue
            self.batch.seen.add(real)
            self.batch.videos.append(video)
            self.batch.status[video] = "queued"
            try:
                size = human(video.stat().st_size)
            except OSError:
                size = "?"
            table.add_row(str(len(self.batch.videos)), video.name, size, "queued", "", key=str(video))
            added += 1
        self.write_log(f"Queued {added} file(s)." if added else "Nothing new to queue.")

    def action_clear(self) -> None:
        if self.batch.running:
            self.write_log("Can't clear the queue while encoding.", "yellow")
            return
        self.batch.videos.clear()
        self.batch.status.clear()
        self.batch.seen.clear()
        self.query_one("#queue", DataTable).clear()
        self.write_log("Queue cleared.")

    # -- running --------------------------------------------------------------

    @on(Button.Pressed, "#start")
    def _start_pressed(self) -> None:
        self.action_start()

    @on(Button.Pressed, "#clear")
    def _clear_pressed(self) -> None:
        self.action_clear()

    def action_start(self) -> None:
        if self.batch.running:
            return
        problems = missing_tools()
        if problems:
            self.write_log(f"{' and '.join(problems)} not found — install with `brew install ffmpeg`", "red")
            return
        pending = [v for v in self.batch.videos if self.batch.status.get(v) == "queued"]
        if not pending:
            self.write_log("Nothing queued to encode.", "yellow")
            return
        self.batch.running = True
        self.batch.failed = 0
        self.batch.stop.clear()
        self.query_one("#start", Button).disabled = True
        self.write_log(f"Compressing {len(pending)} video(s) to HEVC .mp4 (saved next to the originals)…")
        self._encode(pending, self.query_one("#keep-larger", Switch).value)

    @work(thread=True, group="encode")
    def _encode(self, pending: list[Path], keep_larger: bool) -> None:
        adapter = ProgressAdapter(
            on_progress=lambda desc, total, done: self._ui(self._show_progress, desc, total, done),
            on_note=lambda message, style: self._ui(self.write_log, message, style),
        )

        def on_start(i: int, total: int, video: Path) -> None:
            self._ui(self._set_status, video, "encoding")

        def on_result(i: int, total: int, video: Path, result: EncodeResult, cancelled: bool) -> None:
            status, detail = describe_result(result, cancelled)
            self._ui(self._set_status, video, status, detail)
            if status == "failed":
                self._ui(self.write_log, f"{video.name}: failed ({detail}) — see the log file", "red")

        results = run_batch(
            pending,
            keep_larger=keep_larger,
            progress=adapter,
            stop=self.batch.stop,
            on_start=on_start,
            on_result=on_result,
        )
        self._ui(self._finished, results)

    def _show_progress(self, description: str, total: float | None, completed: float) -> None:
        self.query_one("#current", Static).update(Text(description))
        bar = self.query_one("#bar", ProgressBar)
        if total:
            bar.update(total=total, progress=min(completed, total))
        else:
            bar.update(total=None)

    def _finished(self, results: list[EncodeResult]) -> None:
        cancelled = self.batch.stop.is_set()
        self.batch.failed = sum(1 for r in results if not r.ok)
        # Files that never started (after a stop) are still "queued" for a later run.
        self.batch.running = False
        self.query_one("#current", Static).update("")
        self.query_one("#bar", ProgressBar).update(total=100, progress=0)
        self.query_one("#start", Button).disabled = bool(missing_tools())
        self.write_log(summarize(results, cancelled), "red" if self.batch.failed and not cancelled else None)
        if self.batch.quit_requested:
            self.exit(return_code=1 if self.batch.failed and not cancelled else 0)

    # -- quitting -------------------------------------------------------------

    async def action_quit(self) -> None:
        if not self.batch.running:
            self.exit()
            return
        # Stop cleanly: no next file, kill the running ffmpeg and remove its
        # partial output (the CLI's signal handler does the same job). The
        # timer repeats the kill in case ffmpeg was only just being spawned.
        self.batch.quit_requested = True
        self.batch.stop.set()
        self.write_log("Stopping…", "yellow")
        cancel_current()
        self.set_interval(KILL_RETRY_SECONDS, self._kill_tick)

    def _kill_tick(self) -> None:
        if self.batch.running:
            cancel_current()
