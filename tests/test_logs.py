from __future__ import annotations

from pathlib import Path

import pytest

from compress_mov import logs


def test_log_appends_timestamped_line(isolated_log: Path) -> None:
    logs.log("hello")

    assert isolated_log.read_text().rstrip().endswith("hello")


def test_log_block_indents_each_line(isolated_log: Path) -> None:
    logs.log_block("ffmpeg: a\nb\n")

    assert isolated_log.read_text() == "  ffmpeg: a\n  b\n"


def test_logging_never_raises_when_the_log_dir_is_unwritable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("a file where the log directory should be")
    monkeypatch.setattr(logs, "LOG_DIR", blocker / "logs")
    monkeypatch.setattr(logs, "LOG_FILE", blocker / "logs" / "compress.log")

    logs.log("still fine")
    logs.log_block("still fine")
