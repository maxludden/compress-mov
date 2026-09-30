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
