from __future__ import annotations

from pathlib import Path

import pytest
from rich.progress import Progress

from compress_mov import encode as encode_module
from compress_mov.encode import encode_one


class _FakeProc:
    """Stands in for the ffmpeg subprocess.Popen handle."""

    def __init__(self, lines: list[str], rc: int) -> None:
        self.stdout = iter(lines)
        self._rc = rc

    def wait(self) -> int:
        return self._rc


def _fake_popen(lines: list[str], rc: int, *, write_output: bool = True):
    """Build a fake for subprocess.Popen that mimics ffmpeg's observable
    effects (writing the output file, emitting -progress lines) without
    actually invoking ffmpeg.
    """

    def popen(cmd: list[str], **kwargs: object) -> _FakeProc:
        out_path = Path(cmd[-1])
        if write_output:
            out_path.write_bytes(b"0" * 512)
        return _FakeProc(lines, rc)

    return popen


@pytest.fixture(autouse=True)
def stub_probe_duration(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(encode_module, "probe_duration", lambda path: 5.0)
    monkeypatch.setattr(encode_module, "probe_streams", lambda path: [])


def test_encode_one_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    in_path = tmp_path / "clip.mov"
    in_path.write_bytes(b"1" * 1024)
    work_dir = tmp_path / "work"
    work_dir.mkdir()

    lines = ["out_time_us=2500000\n", "speed=1.8x\n", "progress=end\n"]
    monkeypatch.setattr(encode_module.subprocess, "Popen", _fake_popen(lines, rc=0))

    result = encode_one(1, 1, in_path, work_dir, Progress())

    assert result.ok is True
    assert result.in_bytes == 1024
    assert result.out_bytes == 512
    assert result.rc == 0
    assert (tmp_path / "clip (HEVC).mp4").exists()


def test_encode_one_suffixes_output_on_collision(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    in_path = tmp_path / "clip.mov"
    in_path.write_bytes(b"1" * 1024)
    (tmp_path / "clip (HEVC).mp4").write_bytes(b"existing")
    work_dir = tmp_path / "work"
    work_dir.mkdir()

    lines = ["out_time_us=1000000\n", "progress=end\n"]
    monkeypatch.setattr(encode_module.subprocess, "Popen", _fake_popen(lines, rc=0))

    result = encode_one(1, 1, in_path, work_dir, Progress())

    assert result.ok is True
    outputs = sorted(p.name for p in tmp_path.glob("clip (HEVC)*.mp4"))
    # The pre-existing file is left untouched; the new one gets a
    # timestamp-suffixed name instead of overwriting it.
    assert len(outputs) == 2
    assert "clip (HEVC).mp4" in outputs


def test_encode_one_failure_cleans_up_partial_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    in_path = tmp_path / "clip.mov"
    in_path.write_bytes(b"1" * 1024)
    work_dir = tmp_path / "work"
    work_dir.mkdir()

    lines = ["out_time_us=500000\n"]
    monkeypatch.setattr(encode_module.subprocess, "Popen", _fake_popen(lines, rc=1))

    result = encode_one(1, 1, in_path, work_dir, Progress())

    assert result.ok is False
    assert result.in_bytes == 1024
    assert result.out_bytes == 0
    assert result.rc == 1
    assert not (tmp_path / "clip (HEVC).mp4").exists()


def test_encode_one_writes_ffmpeg_stderr_to_log_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, isolated_log: Path
) -> None:
    in_path = tmp_path / "clip.mov"
    in_path.write_bytes(b"1" * 1024)
    work_dir = tmp_path / "work"
    work_dir.mkdir()

    def popen(cmd: list[str], stderr, **kwargs: object) -> _FakeProc:
        stderr.write("Error opening input file\n")
        return _FakeProc([], rc=1)

    monkeypatch.setattr(encode_module.subprocess, "Popen", popen)

    encode_one(1, 1, in_path, work_dir, Progress())

    assert "Error opening input file" in isolated_log.read_text()


def _capturing_popen(rcs: list[int], seen: list[list[str]]):
    """Popen fake that records each command and returns the queued exit codes."""

    def popen(cmd: list[str], **kwargs: object) -> _FakeProc:
        seen.append(cmd)
        rc = rcs.pop(0)
        if rc == 0:
            Path(cmd[-1]).write_bytes(b"0" * 512)
        return _FakeProc([], rc)

    return popen


def test_encode_one_passes_stream_plan_to_ffmpeg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    in_path = tmp_path / "clip.mov"
    in_path.write_bytes(b"1" * 1024)
    (tmp_path / "work").mkdir()
    hdr = {"index": 0, "codec_type": "video", "pix_fmt": "yuv420p10le", "color_transfer": "arib-std-b67"}
    monkeypatch.setattr(encode_module, "probe_streams", lambda path: [hdr])
    seen: list[list[str]] = []
    monkeypatch.setattr(encode_module.subprocess, "Popen", _capturing_popen([0], seen))

    result = encode_one(1, 1, in_path, tmp_path / "work", Progress())

    assert result.ok
    cmd = seen[0]
    assert cmd[cmd.index("-pix_fmt:v:0") + 1] == "yuv420p10le"
    assert cmd[cmd.index("-color_trc:v:0") + 1] == "arib-std-b67"
    assert cmd[cmd.index("-c:v") + 1] == "libx265"


def test_encode_one_retries_without_extras_when_container_rejects_them(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, isolated_log: Path
) -> None:
    in_path = tmp_path / "clip.mov"
    in_path.write_bytes(b"1" * 1024)
    (tmp_path / "work").mkdir()
    src = [
        {"index": 0, "codec_type": "video", "pix_fmt": "yuv420p"},
        {"index": 1, "codec_type": "data", "codec_tag_string": "mebx"},
    ]
    monkeypatch.setattr(encode_module, "probe_streams", lambda path: src)
    seen: list[list[str]] = []
    monkeypatch.setattr(encode_module.subprocess, "Popen", _capturing_popen([234, 0], seen))

    result = encode_one(1, 1, in_path, tmp_path / "work", Progress())

    assert result.ok
    assert len(seen) == 2
    assert "0:1" in seen[0] and "0:1" not in seen[1]
    log = isolated_log.read_text()
    assert "RETRY" in log and "mebx" in log
    assert "WARN" in log and "dropped" in log


def test_encode_one_does_not_retry_when_nothing_best_effort_was_mapped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    in_path = tmp_path / "clip.mov"
    in_path.write_bytes(b"1" * 1024)
    (tmp_path / "work").mkdir()
    monkeypatch.setattr(encode_module, "probe_streams", lambda path: [{"index": 0, "codec_type": "video"}])
    seen: list[list[str]] = []
    monkeypatch.setattr(encode_module.subprocess, "Popen", _capturing_popen([1], seen))

    result = encode_one(1, 1, in_path, tmp_path / "work", Progress())

    assert not result.ok and result.rc == 1
    assert len(seen) == 1


def test_encode_one_reports_failure_if_retry_also_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    in_path = tmp_path / "clip.mov"
    in_path.write_bytes(b"1" * 1024)
    (tmp_path / "work").mkdir()
    src = [{"index": 0, "codec_type": "video"}, {"index": 1, "codec_type": "data", "codec_tag_string": "mebx"}]
    monkeypatch.setattr(encode_module, "probe_streams", lambda path: src)
    seen: list[list[str]] = []
    monkeypatch.setattr(encode_module.subprocess, "Popen", _capturing_popen([234, 1], seen))

    result = encode_one(1, 1, in_path, tmp_path / "work", Progress())

    assert not result.ok and result.rc == 1
    assert len(seen) == 2
    assert not (tmp_path / "clip (HEVC).mp4").exists()
