from __future__ import annotations

from pathlib import Path

import pytest

from compress_mov import logs


@pytest.fixture(autouse=True)
def isolated_log(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect the run log into a scratch dir so tests never touch
    ~/.config/logs/compress.log.
    """
    log_dir = tmp_path / "logs"
    log_file = log_dir / "compress.log"
    monkeypatch.setattr(logs, "LOG_DIR", log_dir)
    monkeypatch.setattr(logs, "LOG_FILE", log_file)
    return log_file
