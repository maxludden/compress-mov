from __future__ import annotations

from contextlib import nullcontext
from pathlib import Path

import pytest
from typer.testing import CliRunner

from compress_mov import cli
from compress_mov.encode import EncodeResult

runner = CliRunner()


@pytest.fixture(autouse=True)
def isolate_process_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep CLI runs from touching signal handlers, caffeinate or notifications."""
    monkeypatch.setattr(cli.signal, "signal", lambda *a, **k: None)
    monkeypatch.setattr(cli, "caffeinate", lambda: nullcontext())
    monkeypatch.setattr(cli, "missing_tools", lambda: [])
    monkeypatch.delenv("COMPRESS_MOV_LAUNCHED", raising=False)


def _invoke(*args: str):
    return runner.invoke(cli.app, list(args))


def test_exits_with_clear_message_when_ffmpeg_missing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    (tmp_path / "a.mov").touch()
    monkeypatch.setattr(cli, "missing_tools", lambda: ["ffmpeg", "ffprobe"])
    called = []
    monkeypatch.setattr(cli, "encode_one", lambda *a, **k: called.append(a))

    result = _invoke(str(tmp_path))

    assert result.exit_code == 1
    assert "ffmpeg and ffprobe not found" in result.output
    assert "brew install ffmpeg" in result.output
    assert called == []


def test_exits_1_when_no_mov_files_found(tmp_path: Path) -> None:
    result = _invoke(str(tmp_path))

    assert result.exit_code == 1
    assert "no .mov files found" in result.output


def test_appledouble_sidecars_are_not_encoded(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    (tmp_path / "IMG_1.mov").write_bytes(b"x")
    (tmp_path / "._IMG_1.mov").write_bytes(b"x")
    seen: list[str] = []

    def fake_encode(i: int, total: int, video: Path, work_dir: Path, progress: object) -> EncodeResult:
        seen.append(video.name)
        return EncodeResult(True, 1000, 400, 1.0, 0)

    monkeypatch.setattr(cli, "encode_one", fake_encode)

    result = _invoke(str(tmp_path))

    assert result.exit_code == 0
    assert seen == ["IMG_1.mov"]


def test_success_reports_savings_and_exits_0(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    (tmp_path / "a.mov").write_bytes(b"x")
    monkeypatch.setattr(cli, "encode_one", lambda *a, **k: EncodeResult(True, 1000, 250, 3.0, 0))

    result = _invoke(str(tmp_path))

    assert result.exit_code == 0
    assert "75.0% smaller" in result.output


def test_batch_summary_and_failure_exit_code(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    (tmp_path / "a.mov").write_bytes(b"x")
    (tmp_path / "b.mov").write_bytes(b"x")
    results = iter([EncodeResult(True, 2000, 1000, 1.0, 0), EncodeResult(False, 500, 0, 1.0, 1)])
    monkeypatch.setattr(cli, "encode_one", lambda *a, **k: next(results))

    result = _invoke(str(tmp_path))

    assert result.exit_code == 1
    assert "failed (exit 1)" in result.output
    assert "1/2 compressed" in result.output
    assert "1 failed" in result.output


def test_recursive_flag_reaches_discovery(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "nested.mov").write_bytes(b"x")
    monkeypatch.setattr(cli, "encode_one", lambda *a, **k: EncodeResult(True, 100, 50, 1.0, 0))

    assert _invoke(str(tmp_path)).exit_code == 1
    assert _invoke("-r", str(tmp_path)).exit_code == 0


def test_notifies_only_when_launched_from_quick_action(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    (tmp_path / "a.mov").write_bytes(b"x")
    monkeypatch.setattr(cli, "encode_one", lambda *a, **k: EncodeResult(True, 100, 50, 1.0, 0))
    messages: list[tuple[str, str]] = []
    monkeypatch.setattr(cli, "notify", lambda title, msg: messages.append((title, msg)))

    _invoke(str(tmp_path))
    assert messages == []

    monkeypatch.setenv("COMPRESS_MOV_LAUNCHED", "1")
    _invoke(str(tmp_path))
    assert messages == [("compress-mov", "Finished compressing 1 video(s)")]


def test_run_without_arguments_prints_usage(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(cli.sys, "argv", ["compress-mov"])

    with pytest.raises(SystemExit) as exc:
        cli.run()

    assert exc.value.code == 2
    assert "usage:" in capsys.readouterr().err


def test_run_relaunches_in_terminal_when_headless(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli.sys, "argv", ["compress-mov", "-r", "/videos"])
    monkeypatch.setattr(cli, "should_relaunch", lambda: True)
    relaunched: list[list[str]] = []
    monkeypatch.setattr(cli, "relaunch_in_terminal", lambda argv: relaunched.append(argv))
    monkeypatch.setattr(cli, "app", lambda: pytest.fail("app() must not run when relaunching"))

    with pytest.raises(SystemExit) as exc:
        cli.run()

    assert exc.value.code == 0
    assert relaunched == [["-r", "/videos"]]


def test_run_runs_app_directly_when_interactive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli.sys, "argv", ["compress-mov", "x.mov"])
    monkeypatch.setattr(cli, "should_relaunch", lambda: False)
    monkeypatch.setattr(cli, "relaunch_in_terminal", lambda argv: pytest.fail("must not relaunch"))
    ran: list[bool] = []
    monkeypatch.setattr(cli, "app", lambda: ran.append(True))

    cli.run()

    assert ran == [True]
