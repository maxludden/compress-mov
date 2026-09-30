"""End-to-end checks against a real ffmpeg. Skipped where it isn't installed
(CI runners and dev machines without it still run the mocked suite)."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from rich.progress import Progress

from compress_mov import bins
from compress_mov import encode as encode_module
from compress_mov.encode import encode_one

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")

pytestmark = pytest.mark.skipif(not (FFMPEG and FFPROBE), reason="ffmpeg/ffprobe not installed")


@pytest.fixture(autouse=True)
def real_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bins, "FFMPEG", FFMPEG)
    monkeypatch.setattr(bins, "FFPROBE", FFPROBE)
    monkeypatch.setattr(encode_module, "FFMPEG", FFMPEG)
    monkeypatch.setattr(encode_module, "FFPROBE", FFPROBE)
    from compress_mov import streams

    monkeypatch.setattr(streams, "FFPROBE", FFPROBE)


def _run(*args: str) -> None:
    subprocess.run([FFMPEG, "-v", "error", "-y", *args], check=True)  # type: ignore[list-item]


def _streams(path: Path) -> list[dict[str, object]]:
    out = subprocess.run(
        [
            FFPROBE,
            "-v",
            "error",
            "-show_entries",
            "stream=codec_type,codec_name:stream_disposition=attached_pic",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return json.loads(out)["streams"]


def test_cover_art_is_copied_not_dropped_or_reencoded(tmp_path: Path) -> None:
    """Regression: with the codec options in the wrong order the cover was
    encoded as HEVC, the mux failed, and the retry silently dropped it."""
    _run("-f", "lavfi", "-i", "color=c=red:s=64x64", "-frames:v", "1", str(tmp_path / "cover.png"))
    src = tmp_path / "clip.mp4"  # mov can't carry attached pictures; encode_one accepts any input
    _run(
        *["-f", "lavfi", "-i", "testsrc2=s=160x90:d=1:r=24", "-f", "lavfi", "-i", "sine=d=1"],
        *["-i", str(tmp_path / "cover.png"), "-map", "0", "-map", "1", "-map", "2"],
        *["-c:v:0", "libx264", "-c:a", "aac", "-c:v:1", "copy", "-disposition:v:1", "attached_pic", str(src)],
    )
    (tmp_path / "work").mkdir()

    result = encode_one(1, 1, src, tmp_path / "work", Progress(), keep_larger=True)

    assert result.ok and result.rc == 0
    out = _streams(tmp_path / "clip (HEVC).mp4")
    kinds = [(s["codec_type"], s["codec_name"], s["disposition"]["attached_pic"]) for s in out]  # type: ignore[index]
    assert ("video", "hevc", 0) in kinds
    assert ("video", "png", 1) in kinds, f"cover art must be copied through, got {kinds}"
    assert ("audio", "aac", 0) in kinds
