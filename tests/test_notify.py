from __future__ import annotations

import subprocess

import pytest

from compress_mov import notify as notify_module


def _capture(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    calls: list[list[str]] = []
    monkeypatch.setattr(notify_module.subprocess, "run", lambda cmd, **kw: calls.append(cmd))
    return calls


def test_title_and_message_are_argv_not_script_source(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _capture(monkeypatch)

    notify_module.notify("compress-mov", "Finished compressing 3 video(s)")

    (cmd,) = calls
    assert cmd[0] == notify_module.OSASCRIPT
    assert cmd[-2:] == ["compress-mov", "Finished compressing 3 video(s)"]
    script = [cmd[i + 1] for i, a in enumerate(cmd) if a == "-e"]
    assert script == list(notify_module._SCRIPT)
    assert not any("Finished" in line for line in script)


@pytest.mark.parametrize("message", ['say "hi"', "back\\slash", 'x" & (do shell script "id") & "', "line1\nline2"])
def test_hostile_text_never_reaches_the_script(monkeypatch: pytest.MonkeyPatch, message: str) -> None:
    calls = _capture(monkeypatch)

    notify_module.notify("t", message)

    cmd = calls[0]
    assert cmd[-1] == message
    assert all(message not in cmd[i + 1] for i, a in enumerate(cmd) if a == "-e")


def test_missing_osascript_is_not_fatal(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*a: object, **k: object) -> subprocess.CompletedProcess[str]:
        raise FileNotFoundError

    monkeypatch.setattr(notify_module.subprocess, "run", boom)

    notify_module.notify("t", "m")
