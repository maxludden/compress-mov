from __future__ import annotations

from pathlib import Path

import pytest

from compress_mov import bins


def _make_exe(path: Path) -> Path:
    path.write_text("#!/bin/sh\n")
    path.chmod(0o755)
    return path


def test_find_binary_prefers_path_lookup(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    on_path = _make_exe(tmp_path / "ffmpeg")
    monkeypatch.setattr(bins.shutil, "which", lambda name: str(on_path))

    assert bins.find_binary("ffmpeg", search_dirs=()) == str(on_path)


def test_find_binary_falls_back_to_search_dirs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(bins.shutil, "which", lambda name: None)
    intel = tmp_path / "usr-local"
    intel.mkdir()
    exe = _make_exe(intel / "ffmpeg")

    found = bins.find_binary("ffmpeg", search_dirs=(str(tmp_path / "opt-homebrew"), str(intel)))

    assert found == str(exe)


def test_find_binary_ignores_non_executable(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(bins.shutil, "which", lambda name: None)
    (tmp_path / "ffmpeg").write_text("not executable")
    (tmp_path / "ffmpeg").chmod(0o644)

    assert bins.find_binary("ffmpeg", search_dirs=(str(tmp_path),)) is None


def test_find_binary_returns_none_when_absent(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(bins.shutil, "which", lambda name: None)

    assert bins.find_binary("ffmpeg", search_dirs=(str(tmp_path),)) is None


def test_missing_tools_reports_each_missing_binary(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    ffmpeg = _make_exe(tmp_path / "ffmpeg")
    monkeypatch.setattr(bins, "FFMPEG", str(ffmpeg))
    monkeypatch.setattr(bins, "FFPROBE", str(tmp_path / "ffprobe"))

    assert bins.missing_tools() == ["ffprobe"]


def test_missing_tools_empty_when_both_present(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(bins, "FFMPEG", str(_make_exe(tmp_path / "ffmpeg")))
    monkeypatch.setattr(bins, "FFPROBE", str(_make_exe(tmp_path / "ffprobe")))

    assert bins.missing_tools() == []
