from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from compress_mov import streams as streams_module
from compress_mov.streams import (
    audio_bitrate_kbps,
    has_alpha,
    is_deep_color,
    plan_streams,
    probe_streams,
)


def video(index: int = 0, **kw: Any) -> dict[str, Any]:
    return {"index": index, "codec_type": "video", "codec_name": "h264", "pix_fmt": "yuv420p", **kw}


def audio(index: int, channels: int = 2) -> dict[str, Any]:
    return {"index": index, "codec_type": "audio", "codec_name": "aac", "channels": channels}


def sub(index: int, codec: str) -> dict[str, Any]:
    return {"index": index, "codec_type": "subtitle", "codec_name": codec}


def pairs(args: list[str]) -> list[tuple[str, str]]:
    return list(zip(args[::2], args[1::2], strict=True))


# --- 10-bit / HDR (item 5) -------------------------------------------------


def test_sdr_8bit_stays_yuv420p() -> None:
    plan = plan_streams([video()])

    assert ("-pix_fmt:v:0", "yuv420p") in pairs(plan.args)


@pytest.mark.parametrize("pix_fmt", ["yuv420p10le", "yuv422p10le", "p010le", "yuv444p12le"])
def test_deep_pix_fmt_uses_10bit_output(pix_fmt: str) -> None:
    plan = plan_streams([video(pix_fmt=pix_fmt)])

    assert ("-pix_fmt:v:0", "yuv420p10le") in pairs(plan.args)


def test_bits_per_raw_sample_wins_over_pix_fmt() -> None:
    assert is_deep_color({"pix_fmt": "yuv420p", "bits_per_raw_sample": "10"})
    assert not is_deep_color({"pix_fmt": "yuv420p10le", "bits_per_raw_sample": "8"})


@pytest.mark.parametrize("transfer", ["arib-std-b67", "smpte2084"])
def test_hdr_transfer_forces_10bit_even_when_tagged_8bit(transfer: str) -> None:
    plan = plan_streams([video(pix_fmt="yuv420p", color_transfer=transfer)])

    assert ("-pix_fmt:v:0", "yuv420p10le") in pairs(plan.args)


def test_colour_tags_are_preserved() -> None:
    plan = plan_streams(
        [video(pix_fmt="yuv420p10le", color_primaries="bt2020", color_transfer="arib-std-b67", color_space="bt2020nc", color_range="tv")]
    )

    got = dict(pairs(plan.args))
    assert got["-color_primaries:v:0"] == "bt2020"
    assert got["-color_trc:v:0"] == "arib-std-b67"
    assert got["-colorspace:v:0"] == "bt2020nc"
    assert got["-color_range:v:0"] == "tv"


def test_unknown_colour_tags_are_not_passed() -> None:
    plan = plan_streams([video(color_primaries="unknown", color_transfer="unspecified", color_space="")])

    assert not any(a.startswith("-color") for a in plan.args)


@pytest.mark.parametrize("pix_fmt", ["yuva444p10le", "yuva422p", "rgba", "argb", "gbrap12le"])
def test_alpha_is_detected_and_warned(pix_fmt: str) -> None:
    assert has_alpha({"pix_fmt": pix_fmt})
    plan = plan_streams([video(pix_fmt=pix_fmt)])

    assert any("alpha" in w for w in plan.warnings)


def test_opaque_formats_are_not_alpha() -> None:
    assert not has_alpha({"pix_fmt": "yuv420p10le"})
    assert not has_alpha({"pix_fmt": "p010le"})


# --- stream retention (item 6) ---------------------------------------------


def test_every_video_stream_is_mapped_and_tagged_hvc1() -> None:
    plan = plan_streams([video(0), video(1, pix_fmt="yuv420p10le")])

    p = pairs(plan.args)
    assert p.count(("-map", "0:0")) == 1 and p.count(("-map", "0:1")) == 1
    assert ("-pix_fmt:v:0", "yuv420p") in p
    assert ("-pix_fmt:v:1", "yuv420p10le") in p
    assert ("-tag:v:0", "hvc1") in p and ("-tag:v:1", "hvc1") in p
    assert plan.warnings == []


def test_all_audio_streams_are_mapped_with_per_stream_bitrates() -> None:
    plan = plan_streams([video(0), audio(1, channels=2), audio(2, channels=6)])

    p = pairs(plan.args)
    assert ("-map", "0:1") in p and ("-map", "0:2") in p
    assert ("-b:a:0", "128k") in p
    assert ("-b:a:1", "384k") in p
    assert ("-c:a", "aac") in p


@pytest.mark.parametrize(("channels", "kbps"), [(0, 128), (1, 128), (2, 128), (6, 384), (8, 512), (24, 512)])
def test_audio_bitrate_scales_with_channels(channels: int, kbps: int) -> None:
    assert audio_bitrate_kbps(channels) == kbps


def test_text_subtitles_become_mov_text() -> None:
    plan = plan_streams([video(0), sub(1, "subrip"), sub(2, "mov_text")])

    p = pairs(plan.args)
    assert ("-c:s:0", "mov_text") in p and ("-c:s:1", "mov_text") in p
    assert plan.extras == [] and plan.warnings == []


def test_bitmap_subtitles_are_stream_copied_as_extras() -> None:
    plan = plan_streams([video(0), sub(1, "dvd_subtitle")])

    assert ("-c:s:0", "copy") in pairs(plan.args)
    assert plan.extras == ["subtitle stream 1 (dvd_subtitle)"]


def test_without_extras_bitmap_subs_are_dropped_with_warning_and_text_subs_reindexed() -> None:
    src = [video(0), sub(1, "dvd_subtitle"), sub(2, "subrip")]

    plan = plan_streams(src, keep_extras=False)

    p = pairs(plan.args)
    assert ("-map", "0:1") not in p
    assert ("-map", "0:2") in p and ("-c:s:0", "mov_text") in p  # index counts mapped subs only
    assert plan.extras == []
    assert any("dvd_subtitle" in w and "dropped" in w for w in plan.warnings)


def test_timecode_track_is_not_mapped_but_not_reported_lost() -> None:
    # ffmpeg rebuilds tmcd from the video stream's `timecode` tag; copying
    # the source tmcd stream into .mp4 fails outright.
    tmcd = {"index": 3, "codec_type": "data", "codec_tag_string": "tmcd"}

    plan = plan_streams([video(0), tmcd])

    assert ("-map", "0:3") not in pairs(plan.args)
    assert plan.warnings == [] and plan.extras == []


def test_other_data_tracks_are_best_effort_extras() -> None:
    mebx = {"index": 4, "codec_type": "data", "codec_tag_string": "mebx"}

    kept = plan_streams([video(0), mebx])
    lost = plan_streams([video(0), mebx], keep_extras=False)

    assert ("-map", "0:4") in pairs(kept.args) and ("-c:d", "copy") in pairs(kept.args)
    assert kept.extras == ["data stream 4 (mebx)"]
    assert ("-map", "0:4") not in pairs(lost.args)
    assert any("mebx" in w for w in lost.warnings)


def test_attached_picture_is_copied_not_encoded() -> None:
    cover = video(1, codec_name="mjpeg", disposition={"attached_pic": 1})

    plan = plan_streams([video(0), cover])

    p = pairs(plan.args)
    assert ("-map", "0:1") in p and ("-c:v:1", "copy") in p
    assert ("-tag:v:1", "hvc1") not in p
    assert plan.extras == ["video stream 1 (mjpeg)"]


def test_no_video_stream_warns() -> None:
    plan = plan_streams([audio(0)])

    assert any("no video stream" in w for w in plan.warnings)


def test_unprobeable_input_falls_back_to_conservative_defaults() -> None:
    plan = plan_streams([])

    assert plan.args[:4] == ["-map", "0:v:0", "-map", "0:a?"]
    assert ("-tag:v", "hvc1") in pairs(plan.args)
    assert plan.warnings == [] and plan.extras == []


# --- probe_streams ----------------------------------------------------------


class _Completed:
    def __init__(self, stdout: str) -> None:
        self.stdout = stdout


def test_probe_streams_parses_ffprobe_json(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    payload = json.dumps({"streams": [{"index": 0, "codec_type": "video"}]})
    monkeypatch.setattr(streams_module.subprocess, "run", lambda *a, **k: _Completed(payload))

    assert probe_streams(tmp_path / "x.mov") == [{"index": 0, "codec_type": "video"}]


@pytest.mark.parametrize("stdout", ["", "not json", "[]", '{"streams": "oops"}'])
def test_probe_streams_returns_empty_on_bad_output(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, stdout: str) -> None:
    monkeypatch.setattr(streams_module.subprocess, "run", lambda *a, **k: _Completed(stdout))

    assert probe_streams(tmp_path / "x.mov") == []


def test_probe_streams_returns_empty_when_ffprobe_missing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def boom(*a: object, **k: object) -> None:
        raise FileNotFoundError

    monkeypatch.setattr(streams_module.subprocess, "run", boom)

    assert probe_streams(tmp_path / "x.mov") == []
