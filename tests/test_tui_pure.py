"""TUI pieces that don't need Textual: args, progress adapter, batch runner."""

from __future__ import annotations

import threading
from pathlib import Path

import pytest
from rich.progress import TaskID

from compress_mov import encode as encode_module
from compress_mov.encode import EncodeResult
from compress_mov.tui import progress as progress_module
from compress_mov.tui import runner as runner_module
from compress_mov.tui.args import TuiArgs, parse_args
from compress_mov.tui.progress import ProgressAdapter
from compress_mov.tui.runner import describe_result, run_batch, summarize

# --- args ---------------------------------------------------------------------


def test_args_defaults() -> None:
    assert parse_args([]) == TuiArgs(paths=[], recursive=False, keep_larger=False)


def test_args_paths_and_flags() -> None:
    args = parse_args(["-r", "--keep-larger", "a.mov", "/some/dir"])

    assert args == TuiArgs(paths=[Path("a.mov"), Path("/some/dir")], recursive=True, keep_larger=True)


def test_args_help_exits_cleanly(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        parse_args(["--help"])

    assert exc.value.code == 0
    assert "compress-mov-tui" in capsys.readouterr().out


def test_args_reject_unknown_options() -> None:
    with pytest.raises(SystemExit) as exc:
        parse_args(["--nope"])

    assert exc.value.code == 2


# --- progress adapter ----------------------------------------------------------


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    now = [100.0]
    monkeypatch.setattr(progress_module.time, "monotonic", lambda: now[0])
    return now


def test_adapter_reports_task_lifecycle(clock: list[float]) -> None:
    events: list[tuple[str, float | None, float]] = []
    adapter = ProgressAdapter(lambda d, t, c: events.append((d, t, c)), lambda m, s: None)

    task = adapter.add_task("[1/1] clip.mov", total=10)
    clock[0] += 1
    adapter.update(task, completed=4, description="[1/1] clip.mov  1.5x")

    assert events == [("[1/1] clip.mov", 10, 0.0), ("[1/1] clip.mov  1.5x", 10, 4)]


def test_adapter_task_ids_are_unique_and_removal_is_safe(clock: list[float]) -> None:
    adapter = ProgressAdapter(lambda *a: None, lambda *a: None)

    first, second = adapter.add_task("a"), adapter.add_task("b")
    adapter.remove_task(first)
    adapter.remove_task(first)  # idempotent
    adapter.update(first, completed=1)  # updating a removed task is ignored

    assert first != second


def test_adapter_throttles_bursts_but_always_reports_the_first(clock: list[float]) -> None:
    events: list[float] = []
    adapter = ProgressAdapter(lambda d, t, c: events.append(c), lambda m, s: None)
    task = adapter.add_task("x", total=100)

    for i in range(1, 6):  # a burst inside the throttle window
        adapter.update(task, completed=i)
    clock[0] += 1
    adapter.update(task, completed=50)

    assert events == [0.0, 50]


def test_adapter_console_forwards_notes() -> None:
    notes: list[tuple[str, str | None]] = []
    adapter = ProgressAdapter(lambda *a: None, lambda m, s: notes.append((m, s)))

    adapter.console.print("careful", style="yellow")
    adapter.console.print("plain")

    assert notes == [("careful", "yellow"), ("plain", None)]


def test_adapter_drives_the_real_encoder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The encoder only needs the ProgressLike surface, so the adapter can replace Rich's Progress."""

    class Proc:
        stdout = iter(["out_time_us=1000000\n", "speed=2.0x\n"])

        def wait(self) -> int:
            return 0

    def popen(cmd: list[str], **kwargs: object) -> Proc:
        Path(cmd[-1]).write_bytes(b"0" * 100)
        return Proc()

    monkeypatch.setattr(encode_module, "probe_duration", lambda p: 4.0)
    monkeypatch.setattr(encode_module, "probe_streams", lambda p: [])
    monkeypatch.setattr(encode_module.subprocess, "Popen", popen)
    clip = tmp_path / "clip.mov"
    clip.write_bytes(b"1" * 1000)
    (tmp_path / "work").mkdir()
    seen: list[tuple[str, float | None, float]] = []
    adapter = ProgressAdapter(lambda d, t, c: seen.append((d, t, c)), lambda m, s: None)

    result = encode_module.encode_one(1, 1, clip, tmp_path / "work", adapter)

    assert result.ok
    assert seen[0] == ("[1/1] clip.mov", 4, 0.0)


# --- batch runner --------------------------------------------------------------


class _NullProgress:
    console = None  # never touched: encode_one is stubbed in these tests


@pytest.fixture
def fake_encode(monkeypatch: pytest.MonkeyPatch) -> list[tuple[int, int, str, bool]]:
    calls: list[tuple[int, int, str, bool]] = []

    def encode_one(i: int, total: int, video: Path, work_dir: Path, progress: object, keep_larger: bool = False):
        assert work_dir.is_dir()
        calls.append((i, total, video.name, keep_larger))
        return EncodeResult(True, 1000, 400, 1.0, 0)

    monkeypatch.setattr(runner_module, "encode_one", encode_one)
    return calls


def test_run_batch_runs_in_order_and_reports_each(fake_encode: list[tuple[int, int, str, bool]]) -> None:
    started: list[str] = []
    finished: list[tuple[str, bool]] = []
    videos = [Path("a.mov"), Path("b.mov")]

    results = run_batch(
        videos,
        keep_larger=True,
        progress=_NullProgress(),  # type: ignore[arg-type]
        stop=threading.Event(),
        on_start=lambda i, t, v: started.append(v.name),
        on_result=lambda i, t, v, r, cancelled: finished.append((v.name, cancelled)),
    )

    assert fake_encode == [(1, 2, "a.mov", True), (2, 2, "b.mov", True)]
    assert started == ["a.mov", "b.mov"]
    assert finished == [("a.mov", False), ("b.mov", False)]
    assert len(results) == 2


def test_run_batch_stops_before_the_next_file(monkeypatch: pytest.MonkeyPatch) -> None:
    stop = threading.Event()
    ran: list[str] = []

    def encode_one(i: int, total: int, video: Path, work_dir: Path, progress: object, keep_larger: bool = False):
        ran.append(video.name)
        stop.set()  # e.g. the user pressed quit during the first file
        return EncodeResult(False, 1000, 0, 1.0, -9)

    monkeypatch.setattr(runner_module, "encode_one", encode_one)
    flags: list[bool] = []

    results = run_batch(
        [Path("a.mov"), Path("b.mov")],
        keep_larger=False,
        progress=_NullProgress(),  # type: ignore[arg-type]
        stop=stop,
        on_start=lambda *a: None,
        on_result=lambda i, t, v, r, cancelled: flags.append(cancelled),
    )

    assert ran == ["a.mov"]
    assert len(results) == 1
    assert flags == [True], "the interrupted file is reported as cancelled, not as a failure"


def test_run_batch_cleans_up_its_temp_dir(monkeypatch: pytest.MonkeyPatch) -> None:
    dirs: list[Path] = []

    def encode_one(i: int, total: int, video: Path, work_dir: Path, progress: object, keep_larger: bool = False):
        dirs.append(work_dir)
        raise RuntimeError("boom")

    monkeypatch.setattr(runner_module, "encode_one", encode_one)

    with pytest.raises(RuntimeError):
        run_batch(
            [Path("a.mov")],
            keep_larger=False,
            progress=_NullProgress(),  # type: ignore[arg-type]
            stop=threading.Event(),
            on_start=lambda *a: None,
            on_result=lambda *a: None,
        )

    assert dirs and not dirs[0].exists()


@pytest.mark.parametrize(
    ("result", "cancelled", "expected"),
    [
        (EncodeResult(True, 1000, 250, 1.0, 0), False, ("done", "250 B (75.0% smaller)")),
        (
            EncodeResult(True, 1000, 1200, 1.0, 0, discarded=True),
            False,
            ("skipped", "no savings (1000 B → 1.2 KB); original kept"),
        ),
        (EncodeResult(False, 1000, 0, 1.0, 1), False, ("failed", "exit 1")),
        (EncodeResult(False, 10, 0, 0.0, -1, error="OSError: disk full"), False, ("failed", "OSError: disk full")),
        (EncodeResult(False, 1000, 0, 1.0, -9), True, ("cancelled", "")),
    ],
)
def test_describe_result(result: EncodeResult, cancelled: bool, expected: tuple[str, str]) -> None:
    assert describe_result(result, cancelled) == expected


def test_summarize() -> None:
    results = [
        EncodeResult(True, 2000, 1000, 1.0, 0),
        EncodeResult(True, 500, 900, 1.0, 0, discarded=True),
        EncodeResult(False, 100, 0, 1.0, 1),
    ]

    assert (
        summarize(results)
        == "Finished: 1 compressed: 2.0 KB → 1000 B (50.0% smaller), 1 skipped (no savings), 1 failed"
    )
    assert summarize(results[:1], cancelled=True).startswith("Stopped early: 1 compressed")
    assert summarize([]) == "Finished: nothing was encoded"


def test_task_id_type_is_rich_compatible() -> None:
    assert isinstance(ProgressAdapter(lambda *a: None, lambda *a: None).add_task("x"), int)
    assert TaskID(3) == 3
