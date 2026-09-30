"""Drives the real Textual app headlessly with its test pilot (no terminal needed)."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Awaitable, Callable
from contextlib import nullcontext
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("textual")

from textual.widgets import Button, DataTable, Input, RichLog, Switch

from compress_mov.encode import EncodeResult
from compress_mov.tui import app as app_module
from compress_mov.tui import runner as runner_module
from compress_mov.tui.app import CompressApp
from compress_mov.tui.args import TuiArgs


def run(coro: Callable[[], Awaitable[None]]) -> None:
    asyncio.run(coro())


def log_text(app: CompressApp) -> str:
    return "\n".join(str(line.text) for line in app.query_one("#log", RichLog).lines)


def table_rows(app: CompressApp) -> list[list[str]]:
    table = app.query_one("#queue", DataTable)
    return [[str(table.get_cell(key, col.key)) for col in table.ordered_columns] for key in table.rows]


async def settle(app: CompressApp, pilot: Any) -> None:
    await pilot.pause()
    await app.workers.wait_for_complete()
    await pilot.pause()


@pytest.fixture(autouse=True)
def hermetic(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(app_module, "missing_tools", lambda: [])
    monkeypatch.setattr(runner_module, "caffeinate", lambda: nullcontext())


@pytest.fixture
def videos(tmp_path: Path) -> Path:
    for name in ("a.mov", "b.mov", "._a.mov", "notes.txt"):
        (tmp_path / name).write_bytes(b"x" * 2048)
    return tmp_path


def ok_result(i: int = 1) -> EncodeResult:
    return EncodeResult(True, 2048, 512, 1.0, 0)


def test_initial_paths_are_scanned_and_queued(videos: Path) -> None:
    async def scenario() -> None:
        app = CompressApp(TuiArgs(paths=[videos]))
        async with app.run_test() as pilot:
            await settle(app, pilot)

            rows = table_rows(app)
            assert [r[1] for r in rows] == ["a.mov", "b.mov"], "sidecars and non-.mov files are skipped"
            assert [r[3] for r in rows] == ["queued", "queued"]
            assert "Queued 2 file(s)." in log_text(app)

    run(scenario)


def test_scan_notes_go_to_the_log_not_over_the_screen(videos: Path, capfd: pytest.CaptureFixture[str]) -> None:
    async def scenario() -> None:
        app = CompressApp(TuiArgs(paths=[videos, videos / "notes.txt", videos / "ghost.mov"]))
        async with app.run_test() as pilot:
            await settle(app, pilot)

            text = log_text(app)
            assert "skipping (not a .mov): notes.txt" in text
            assert "skipping (not found)" in text
            assert "[bold]" not in text

    run(scenario)
    assert "skipping" not in capfd.readouterr().err, "resolve_inputs must not write to the console under the TUI"


def test_adding_a_path_through_the_input_box(videos: Path) -> None:
    async def scenario() -> None:
        app = CompressApp()
        async with app.run_test() as pilot:
            await pilot.press("a")
            assert app.focused is app.query_one("#path", Input)
            for ch in f"'{videos / 'a.mov'}'":  # quoted, as a terminal drag-and-drop would paste it
                await pilot.press(ch)
            await pilot.press("enter")
            await settle(app, pilot)

            assert [r[1] for r in table_rows(app)] == ["a.mov"]
            assert app.query_one("#path", Input).value == ""
            assert isinstance(app.focused, DataTable), "focus returns to the queue so the key bindings work"

    run(scenario)


def test_the_same_file_is_not_queued_twice(videos: Path) -> None:
    async def scenario() -> None:
        app = CompressApp(TuiArgs(paths=[videos / "a.mov", videos]))
        async with app.run_test() as pilot:
            await settle(app, pilot)

            assert [r[1] for r in table_rows(app)] == ["a.mov", "b.mov"]

    run(scenario)


def test_start_encodes_the_queue_and_shows_results(videos: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[str, bool]] = []
    results = {"a.mov": ok_result(), "b.mov": EncodeResult(False, 2048, 0, 1.0, 1)}

    def encode_one(i: int, total: int, video: Path, work_dir: Path, progress: Any, keep_larger: bool = False):
        seen.append((video.name, keep_larger))
        progress.update(progress.add_task(f"[{i}/{total}] {video.name}", total=10), completed=5)
        return results[video.name]

    monkeypatch.setattr(runner_module, "encode_one", encode_one)

    async def scenario() -> None:
        app = CompressApp(TuiArgs(paths=[videos], keep_larger=True))
        async with app.run_test() as pilot:
            await settle(app, pilot)
            await pilot.press("s")
            await settle(app, pilot)

            rows = table_rows(app)
            assert [(r[1], r[3]) for r in rows] == [("a.mov", "done"), ("b.mov", "failed")]
            assert "75.0% smaller" in rows[0][4] and "exit 1" in rows[1][4]
            text = log_text(app)
            assert "Finished: 1 compressed" in text and "1 failed" in text
            assert app.batch.running is False
            assert app.query_one("#start", Button).disabled is False

    run(scenario)
    assert seen == [("a.mov", True), ("b.mov", True)], "the Keep larger switch reaches the encoder"


def test_only_queued_files_run_again_after_a_batch(videos: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ran: list[str] = []

    def encode_one(i: int, total: int, video: Path, work_dir: Path, progress: Any, keep_larger: bool = False):
        ran.append(video.name)
        return ok_result()

    monkeypatch.setattr(runner_module, "encode_one", encode_one)

    async def scenario() -> None:
        app = CompressApp(TuiArgs(paths=[videos / "a.mov"]))
        async with app.run_test() as pilot:
            await settle(app, pilot)
            await pilot.press("s")
            await settle(app, pilot)
            app._enqueue([videos / "b.mov"])
            await pilot.press("s")
            await settle(app, pilot)

    run(scenario)
    assert ran == ["a.mov", "b.mov"], "finished files aren't re-encoded"


def test_start_with_an_empty_queue_only_logs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runner_module, "encode_one", lambda *a, **k: pytest.fail("nothing should run"))

    async def scenario() -> None:
        app = CompressApp()
        async with app.run_test() as pilot:
            await pilot.press("s")
            await settle(app, pilot)

            assert "Nothing queued to encode." in log_text(app)

    run(scenario)


def test_missing_ffmpeg_disables_start_with_a_clear_message(videos: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(app_module, "missing_tools", lambda: ["ffmpeg", "ffprobe"])
    monkeypatch.setattr(runner_module, "encode_one", lambda *a, **k: pytest.fail("must not encode"))

    async def scenario() -> None:
        app = CompressApp(TuiArgs(paths=[videos]))
        async with app.run_test() as pilot:
            await settle(app, pilot)
            assert app.query_one("#start", Button).disabled is True
            assert "ffmpeg and ffprobe not found" in log_text(app)
            await pilot.press("s")
            await settle(app, pilot)
            assert app.batch.running is False

    run(scenario)


def test_clear_empties_the_queue_but_not_while_running(videos: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    release = threading.Event()
    started = threading.Event()

    def encode_one(i: int, total: int, video: Path, work_dir: Path, progress: Any, keep_larger: bool = False):
        started.set()
        release.wait(5)
        return ok_result()

    monkeypatch.setattr(runner_module, "encode_one", encode_one)

    async def scenario() -> None:
        app = CompressApp(TuiArgs(paths=[videos]))
        async with app.run_test() as pilot:
            await settle(app, pilot)
            await pilot.press("s")
            for _ in range(50):
                await pilot.pause(0.05)
                if started.is_set():
                    break
            await pilot.press("x")
            assert len(table_rows(app)) == 2 and "while encoding" in log_text(app)
            release.set()
            await settle(app, pilot)
            await pilot.press("x")
            assert table_rows(app) == [] and app.batch.videos == []

    run(scenario)


def test_quit_when_idle_exits_immediately() -> None:
    async def scenario() -> None:
        app = CompressApp()
        async with app.run_test() as pilot:
            await pilot.press("q")
            await pilot.pause()

            assert not app.is_running

    run(scenario)


def test_quit_while_encoding_stops_ffmpeg_and_exits(videos: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    started = threading.Event()
    killed = threading.Event()
    ran: list[str] = []

    def encode_one(i: int, total: int, video: Path, work_dir: Path, progress: Any, keep_larger: bool = False):
        ran.append(video.name)
        started.set()
        killed.wait(5)  # stands in for a running ffmpeg that only ends once it's killed
        return EncodeResult(False, 2048, 0, 1.0, -9)

    monkeypatch.setattr(runner_module, "encode_one", encode_one)
    monkeypatch.setattr(app_module, "cancel_current", killed.set)

    async def scenario() -> None:
        app = CompressApp(TuiArgs(paths=[videos]))
        async with app.run_test() as pilot:
            await settle(app, pilot)
            await pilot.press("s")
            for _ in range(50):
                await pilot.pause(0.05)
                if started.is_set():
                    break
            await pilot.press("q")
            for _ in range(100):
                await pilot.pause(0.05)
                if not app.is_running:
                    break

            assert not app.is_running, "the app exits once the killed encode returns"
            assert killed.is_set(), "the in-flight ffmpeg is cancelled"
            assert app.batch.stop.is_set()

    run(scenario)
    assert ran == ["a.mov"], "the second file never starts after quit"


def test_switch_values_seed_from_arguments() -> None:
    async def scenario() -> None:
        app = CompressApp(TuiArgs(recursive=True, keep_larger=True))
        async with app.run_test() as pilot:
            await pilot.pause()

            assert app.query_one("#recursive", Switch).value is True
            assert app.query_one("#keep-larger", Switch).value is True

    run(scenario)


def test_recursive_switch_reaches_discovery(tmp_path: Path) -> None:
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "deep.mov").write_bytes(b"x")

    async def scenario() -> None:
        app = CompressApp(TuiArgs(paths=[tmp_path], recursive=True))
        async with app.run_test() as pilot:
            await settle(app, pilot)

            assert [r[1] for r in table_rows(app)] == ["deep.mov"]

    run(scenario)


def test_app_does_not_shadow_textual_internals() -> None:
    """Regression: overriding a private Textual name (we once had `_running` and `_log`) breaks the framework.

    Reads the class body from source, so names Textual's metaclass adds at runtime don't count as ours.
    """
    import ast

    from textual.app import App

    tree = ast.parse(Path(app_module.__file__).read_text())
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "CompressApp")
    defined: set[str] = set()
    for node in cls.body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            defined.add(node.name)
        elif isinstance(node, ast.Assign):
            defined |= {t.id for t in node.targets if isinstance(t, ast.Name)}
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            defined.add(node.target.id)

    assert {n for n in defined if hasattr(App, n)} == {
        "__init__", "compose", "action_quit", "CSS", "TITLE", "SUB_TITLE", "BINDINGS",
    }  # fmt: skip
    # ...and the instance attributes we add don't collide with the framework's either.
    assert set(vars(CompressApp())) - set(vars(App())) == {"_args", "batch"}
