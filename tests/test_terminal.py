from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from compress_mov import terminal
from compress_mov.terminal import build_launcher_script, relaunch_in_terminal, self_command, should_relaunch


class _Stream:
    def __init__(self, tty: bool) -> None:
        self._tty = tty

    def isatty(self) -> bool:
        return self._tty


@pytest.fixture
def headless(monkeypatch: pytest.MonkeyPatch) -> None:
    """A macOS process with no TTYs and none of the opt-out env vars."""
    monkeypatch.setattr(sys, "platform", "darwin")
    for name in ("COMPRESS_MOV_LAUNCHED", "COMPRESS_MOV_NO_RELAUNCH", "SSH_CONNECTION"):
        monkeypatch.delenv(name, raising=False)
    for name in ("stdin", "stdout", "stderr"):
        monkeypatch.setattr(sys, name, _Stream(False))


def test_should_relaunch_when_fully_headless_on_macos(headless: None) -> None:
    assert should_relaunch() is True


@pytest.mark.parametrize("stream", ["stdin", "stdout", "stderr"])
def test_should_not_relaunch_if_any_stream_is_a_tty(headless: None, monkeypatch: pytest.MonkeyPatch, stream: str) -> None:
    # e.g. `compress-mov x.mov 2>log.txt` from an interactive shell.
    monkeypatch.setattr(sys, stream, _Stream(True))

    assert should_relaunch() is False


@pytest.mark.parametrize("var", ["COMPRESS_MOV_LAUNCHED", "COMPRESS_MOV_NO_RELAUNCH", "SSH_CONNECTION"])
def test_should_not_relaunch_with_opt_out_env(headless: None, monkeypatch: pytest.MonkeyPatch, var: str) -> None:
    monkeypatch.setenv(var, "1")

    assert should_relaunch() is False


def test_should_not_relaunch_off_macos(headless: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")

    assert should_relaunch() is False


def test_self_command_uses_interpreter_module_not_argv0() -> None:
    # Regression: `python -m compress_mov` sets argv[0] to a non-executable
    # __main__.py, so the launcher must not run argv[0] directly.
    assert self_command() == [sys.executable, "-m", "compress_mov"]


def test_build_launcher_script_quotes_arguments(tmp_path: Path) -> None:
    script = build_launcher_script(["/usr/bin/python3", "-m", "compress_mov"], ["-r", "/Users/me/My Videos/it's.mov"], tmp_path)

    assert script.startswith("#!/bin/zsh\n")
    assert f"rm -rf -- {tmp_path}" in script
    assert "COMPRESS_MOV_LAUNCHED=1 /usr/bin/python3 -m compress_mov -r '/Users/me/My Videos/it'\"'\"'s.mov'" in script


def test_relaunch_writes_executable_launcher_and_opens_it(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    monkeypatch.delenv("COMPRESS_MOV_TERMINAL", raising=False)
    calls: list[list[str]] = []
    monkeypatch.setattr(terminal.subprocess, "run", lambda cmd, **kw: calls.append(cmd))
    clip = tmp_path / "clip.mov"

    relaunch_in_terminal(["-r", str(clip)], command=["/opt/py", "-m", "compress_mov"])

    (cmd,) = calls
    assert cmd[:3] == [terminal.OPEN, "-a", "Terminal"]
    launcher = Path(cmd[3])
    assert launcher.name == "compress-mov.command"
    assert launcher.stat().st_mode & 0o111
    text = launcher.read_text()
    assert f"/opt/py -m compress_mov -r {clip}" in text


def test_relaunch_makes_relative_paths_absolute_but_keeps_flags(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    calls: list[list[str]] = []
    monkeypatch.setattr(terminal.subprocess, "run", lambda cmd, **kw: calls.append(cmd))

    relaunch_in_terminal(["-r", "clip.mov"], command=["prog"])

    text = Path(calls[0][3]).read_text()
    assert f"prog -r {(tmp_path / 'clip.mov').resolve()}" in text


def test_relaunch_honours_terminal_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    monkeypatch.setenv("COMPRESS_MOV_TERMINAL", "iTerm")
    calls: list[list[str]] = []
    monkeypatch.setattr(terminal.subprocess, "run", lambda cmd, **kw: calls.append(cmd))

    relaunch_in_terminal(["x.mov"], command=["prog"])

    assert calls[0][:3] == [terminal.OPEN, "-a", "iTerm"]


def test_relaunch_failure_cleans_up_and_exits(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setenv("TMPDIR", str(tmp_path))

    def boom(cmd: list[str], **kw: object) -> None:
        raise subprocess.CalledProcessError(1, cmd)

    monkeypatch.setattr(terminal.subprocess, "run", boom)

    with pytest.raises(SystemExit) as exc:
        relaunch_in_terminal(["x.mov"], command=["prog"])

    assert exc.value.code == 1
    assert "could not open Terminal" in capsys.readouterr().err
    assert list(tmp_path.glob("compress-mov.*")) == []
