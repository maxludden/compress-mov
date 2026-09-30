from __future__ import annotations

from pathlib import Path

import pytest

from compress_mov import encode as encode_module


class _FakeCompleted:
    def __init__(self, stdout: str) -> None:
        self.stdout = stdout


def test_probe_duration_reads_format_duration(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(encode_module.subprocess, "run", lambda args, **kwargs: _FakeCompleted("12.5\n"))

    assert encode_module.probe_duration(tmp_path / "x.mov") == 12.5


def test_probe_duration_falls_back_to_stream_duration(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    outputs = iter(["", "7.0\n"])
    monkeypatch.setattr(encode_module.subprocess, "run", lambda args, **kwargs: _FakeCompleted(next(outputs)))

    assert encode_module.probe_duration(tmp_path / "x.mov") == 7.0


def test_probe_duration_returns_zero_when_undeterminable(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(encode_module.subprocess, "run", lambda args, **kwargs: _FakeCompleted(""))

    assert encode_module.probe_duration(tmp_path / "x.mov") == 0.0


def test_probe_duration_puts_the_path_after_dash_i_in_both_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    """A path starting with "-" must never be readable as an ffprobe option."""
    calls: list[list[str]] = []

    def fake_run(args: list[str], **kwargs: object) -> _FakeCompleted:
        calls.append(args)
        return _FakeCompleted("")  # force the stream-duration fallback too

    monkeypatch.setattr(encode_module.subprocess, "run", fake_run)

    encode_module.probe_duration(Path("-evil.mov"))

    assert len(calls) == 2
    assert all(c[-2:] == ["-i", "-evil.mov"] for c in calls)
