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


def test_encode_one_does_not_retry_when_nothing_best_effort_was_mapped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
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


# --- output naming -----------------------------------------------------------


def test_unique_output_path_counts_up_past_every_collision(tmp_path: Path) -> None:
    in_path = tmp_path / "clip.mov"
    assert encode_module.reserve_output_path(in_path).name == "clip (HEVC).mp4"

    (tmp_path / "clip (HEVC).mp4").touch()
    assert encode_module.reserve_output_path(in_path).name == "clip (HEVC) 2.mp4"

    (tmp_path / "clip (HEVC) 2.mp4").touch()
    (tmp_path / "clip (HEVC) 3.mp4").touch()
    assert encode_module.reserve_output_path(in_path).name == "clip (HEVC) 4.mp4"


def test_repeated_collisions_never_overwrite_earlier_outputs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    in_path = tmp_path / "clip.mov"
    in_path.write_bytes(b"1" * 1024)
    (tmp_path / "work").mkdir()
    monkeypatch.setattr(encode_module.subprocess, "Popen", _fake_popen(["progress=end\n"], rc=0))

    for _ in range(3):
        assert encode_one(1, 1, in_path, tmp_path / "work", Progress()).ok

    assert sorted(p.name for p in tmp_path.glob("*.mp4")) == [
        "clip (HEVC) 2.mp4",
        "clip (HEVC) 3.mp4",
        "clip (HEVC).mp4",
    ]


def test_output_name_is_reserved_before_ffmpeg_starts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    in_path = tmp_path / "clip.mov"
    in_path.write_bytes(b"1" * 1024)
    (tmp_path / "work").mkdir()
    reserved_at_spawn: list[bool] = []

    def popen(cmd: list[str], **kwargs: object) -> _FakeProc:
        reserved_at_spawn.append(Path(cmd[-1]).exists())
        Path(cmd[-1]).write_bytes(b"0" * 512)
        assert "-y" in cmd, "ffmpeg overwrites the placeholder we created, nothing else"
        return _FakeProc([], 0)

    monkeypatch.setattr(encode_module.subprocess, "Popen", popen)

    assert encode_one(1, 1, in_path, tmp_path / "work", Progress()).ok
    assert reserved_at_spawn == [True]


def test_reserving_is_exclusive_and_creates_the_file(tmp_path: Path) -> None:
    in_path = tmp_path / "clip.mov"

    first = encode_module.reserve_output_path(in_path)
    second = encode_module.reserve_output_path(in_path)

    assert (first.name, second.name) == ("clip (HEVC).mp4", "clip (HEVC) 2.mp4")
    assert first.exists() and second.exists()


def test_failure_never_deletes_a_file_this_run_did_not_create(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression: a pre-existing output must survive both failure and retry cleanup."""
    in_path = tmp_path / "clip.mov"
    in_path.write_bytes(b"1" * 1024)
    foreign = tmp_path / "clip (HEVC).mp4"
    foreign.write_bytes(b"someone else's output")
    (tmp_path / "work").mkdir()
    src = [
        {"index": 0, "codec_type": "video"},
        {"index": 1, "codec_type": "data", "codec_tag_string": "mebx"},
    ]
    monkeypatch.setattr(encode_module, "probe_streams", lambda path: src)
    seen: list[list[str]] = []
    monkeypatch.setattr(encode_module.subprocess, "Popen", _capturing_popen([234, 1], seen))

    result = encode_one(1, 1, in_path, tmp_path / "work", Progress())

    assert not result.ok
    assert foreign.read_bytes() == b"someone else's output"
    assert [p.name for p in tmp_path.glob("*.mp4")] == ["clip (HEVC).mp4"], "our own placeholder is cleaned up"


def test_no_placeholder_is_left_behind_when_setup_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    in_path = tmp_path / "clip.mov"
    in_path.write_bytes(b"1" * 1024)
    (tmp_path / "work").mkdir()

    def boom(path: Path) -> list[dict[str, object]]:
        raise RuntimeError("probe blew up")

    monkeypatch.setattr(encode_module, "probe_streams", boom)

    result = encode_one(1, 1, in_path, tmp_path / "work", Progress())

    assert not result.ok
    assert not list(tmp_path.glob("*.mp4"))


# --- larger output -----------------------------------------------------------


@pytest.mark.parametrize("in_size", [100, 512])  # output is 512 bytes: larger, then equal
def test_output_not_smaller_is_discarded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, isolated_log: Path, in_size: int
) -> None:
    in_path = tmp_path / "clip.mov"
    in_path.write_bytes(b"1" * in_size)
    (tmp_path / "work").mkdir()
    monkeypatch.setattr(encode_module.subprocess, "Popen", _fake_popen(["progress=end\n"], rc=0))

    result = encode_one(1, 1, in_path, tmp_path / "work", Progress())

    assert result.ok and result.discarded
    assert result.in_bytes == in_size and result.out_bytes == 512
    assert not list(tmp_path.glob("*.mp4"))
    assert in_path.exists()
    assert "SKIP" in isolated_log.read_text()


def test_keep_larger_retains_the_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    in_path = tmp_path / "clip.mov"
    in_path.write_bytes(b"1" * 100)
    (tmp_path / "work").mkdir()
    monkeypatch.setattr(encode_module.subprocess, "Popen", _fake_popen(["progress=end\n"], rc=0))

    result = encode_one(1, 1, in_path, tmp_path / "work", Progress(), keep_larger=True)

    assert result.ok and not result.discarded
    assert (tmp_path / "clip (HEVC).mp4").exists()


# --- mtime -------------------------------------------------------------------


def test_output_inherits_source_timestamps(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import os

    in_path = tmp_path / "clip.mov"
    in_path.write_bytes(b"1" * 1024)
    old = 1_500_000_000
    os.utime(in_path, (old, old))
    (tmp_path / "work").mkdir()
    monkeypatch.setattr(encode_module.subprocess, "Popen", _fake_popen(["progress=end\n"], rc=0))

    encode_one(1, 1, in_path, tmp_path / "work", Progress())

    st = (tmp_path / "clip (HEVC).mp4").stat()
    assert int(st.st_mtime) == old and int(st.st_atime) == old


def test_failing_to_copy_timestamps_is_not_fatal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    in_path = tmp_path / "clip.mov"
    in_path.write_bytes(b"1" * 1024)
    (tmp_path / "work").mkdir()
    monkeypatch.setattr(encode_module.subprocess, "Popen", _fake_popen(["progress=end\n"], rc=0))

    def boom(*a: object, **k: object) -> None:
        raise PermissionError

    monkeypatch.setattr(encode_module.os, "utime", boom)

    assert encode_one(1, 1, in_path, tmp_path / "work", Progress()).ok


# --- unexpected exceptions ---------------------------------------------------


def test_unexpected_exception_becomes_a_failed_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, isolated_log: Path
) -> None:
    in_path = tmp_path / "clip.mov"
    in_path.write_bytes(b"1" * 1024)
    (tmp_path / "work").mkdir()

    def popen(cmd: list[str], **kwargs: object) -> None:
        Path(cmd[-1]).write_bytes(b"partial")
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(encode_module.subprocess, "Popen", popen)
    progress = Progress()

    result = encode_one(1, 1, in_path, tmp_path / "work", progress)

    assert not result.ok and result.rc == -1
    assert result.in_bytes == 1024
    assert result.error and "No space left" in result.error
    assert not list(tmp_path.glob("*.mp4")), "partial output must be removed"
    assert progress.tasks == [], "the progress task must not be left behind"
    assert "ERROR" in isolated_log.read_text()


def test_one_failing_file_does_not_stop_the_next(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bad, good = tmp_path / "bad.mov", tmp_path / "good.mov"
    bad.write_bytes(b"1" * 1024)
    good.write_bytes(b"1" * 1024)
    (tmp_path / "work").mkdir()
    good_popen = _fake_popen(["progress=end\n"], rc=0)

    def popen(cmd: list[str], **kwargs: object):
        if any(str(a).endswith("bad.mov") for a in cmd):
            raise PermissionError(13, "Permission denied")
        return good_popen(cmd, **kwargs)

    monkeypatch.setattr(encode_module.subprocess, "Popen", popen)

    results = [encode_one(i, 2, p, tmp_path / "work", Progress()) for i, p in enumerate([bad, good], start=1)]

    assert [r.ok for r in results] == [False, True]


def test_unreadable_input_is_a_failed_result_not_a_crash(tmp_path: Path) -> None:
    (tmp_path / "work").mkdir()

    result = encode_one(1, 1, tmp_path / "missing.mov", tmp_path / "work", Progress())

    assert not result.ok and result.in_bytes == 0 and result.error


def test_interrupt_exit_is_not_swallowed_by_the_failure_handler(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    in_path = tmp_path / "clip.mov"
    in_path.write_bytes(b"1" * 1024)
    (tmp_path / "work").mkdir()

    def popen(cmd: list[str], **kwargs: object) -> None:
        raise SystemExit(130)

    monkeypatch.setattr(encode_module.subprocess, "Popen", popen)

    with pytest.raises(SystemExit) as exc:
        encode_one(1, 1, in_path, tmp_path / "work", Progress())

    assert exc.value.code == 130


# --- signal handling ---------------------------------------------------------


class _KillableProc:
    def __init__(self) -> None:
        self.stdout = iter([])
        self.terminated = False
        self.killed = False

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.killed = True

    def wait(self, timeout: float | None = None) -> int:
        return -15 if self.terminated else 0

    def poll(self) -> int | None:
        return None


@pytest.fixture
def clean_current():
    encode_module._CURRENT.proc = None
    encode_module._CURRENT.out_path = None
    encode_module._CURRENT.starting = False
    encode_module._CURRENT.pending = None
    yield encode_module._CURRENT
    encode_module._CURRENT.proc = None
    encode_module._CURRENT.out_path = None
    encode_module._CURRENT.starting = False
    encode_module._CURRENT.pending = None


def test_interrupt_terminates_ffmpeg_and_removes_partial_output(clean_current, tmp_path: Path) -> None:
    proc, out = _KillableProc(), tmp_path / "out.mp4"
    out.write_bytes(b"partial")
    clean_current.proc, clean_current.out_path = proc, out

    with pytest.raises(SystemExit) as exc:
        encode_module.handle_interrupt(15, None)

    assert exc.value.code == 130
    assert proc.terminated and not out.exists()


def test_signal_during_spawn_is_deferred_then_replayed(
    clean_current, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The race: a signal lands while Popen is still returning."""
    out = tmp_path / "out.mp4"
    proc = _KillableProc()

    def popen(cmd: list[str], **kwargs: object) -> _KillableProc:
        out.write_bytes(b"partial")
        # Delivered before we hold the process handle: must not exit yet.
        encode_module.handle_interrupt(15, None)
        assert not proc.terminated
        return proc

    monkeypatch.setattr(encode_module.subprocess, "Popen", popen)

    with pytest.raises(SystemExit) as exc:
        encode_module._spawn(["ffmpeg"], None, out)

    assert exc.value.code == 130
    assert proc.terminated, "the deferred signal must terminate the just-started ffmpeg"
    assert not out.exists()


def test_spawn_without_a_signal_registers_the_process(
    clean_current, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    proc = _KillableProc()
    monkeypatch.setattr(encode_module.subprocess, "Popen", lambda cmd, **kw: proc)
    out = tmp_path / "out.mp4"

    assert encode_module._spawn(["ffmpeg"], None, out) is proc
    assert clean_current.proc is proc and clean_current.out_path == out
    assert clean_current.starting is False


def test_spawn_failure_resets_starting_flag(clean_current, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def popen(cmd: list[str], **kwargs: object) -> None:
        raise FileNotFoundError

    monkeypatch.setattr(encode_module.subprocess, "Popen", popen)

    with pytest.raises(FileNotFoundError):
        encode_module._spawn(["ffmpeg"], None, tmp_path / "out.mp4")

    assert clean_current.starting is False
