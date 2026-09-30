"""Deciding what to keep from a source file: stream mapping, pixel format,
colour tags and audio bitrates.

Kept separate from :mod:`encode` so the decisions are pure functions of
ffprobe's JSON and can be tested without ffmpeg.
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .bins import FFPROBE

# Transfer functions that mean "HDR": 8-bit output would band badly, so these
# always get 10-bit even if the source were tagged 8-bit.
HDR_TRANSFERS = {"smpte2084", "arib-std-b67"}

# Subtitle codecs ffmpeg can convert to mov_text for MP4. Bitmap subtitles
# (dvd_subtitle, hdmv_pgs_subtitle, ...) can't be, and are dropped.
TEXT_SUBTITLE_CODECS = {"mov_text", "subrip", "srt", "ass", "ssa", "webvtt", "text"}

_UNKNOWN = {"", "unknown", "unspecified", "reserved", "n/a"}
_ALPHA_PIX_FMT = re.compile(r"^(yuva|ya\d|argb|abgr|rgba|bgra|gbra)")
_DEEP_PIX_FMT = re.compile(r"(?:p|^)0?(?:9|10|12|14|16)(?:le|be)$")

MIN_AUDIO_KBPS = 128
AUDIO_KBPS_PER_CHANNEL = 64
MAX_AUDIO_CHANNELS = 8  # AAC's limit


@dataclass
class StreamPlan:
    """ffmpeg arguments for the streams, plus what was lost getting there."""

    args: list[str]
    # Content the output genuinely lacks; worth showing to the user.
    warnings: list[str] = field(default_factory=list)
    # Streams mapped on a best-effort basis (stream-copied, not converted).
    # If ffmpeg rejects the output, replan with ``keep_extras=False``.
    extras: list[str] = field(default_factory=list)
    # Routine drops (attachments and the like): log only.
    dropped: list[str] = field(default_factory=list)


def probe_streams(path: Path) -> list[dict[str, Any]]:
    """ffprobe's per-stream info, or [] if it can't be read.

    An empty result makes :func:`plan_streams` fall back to the conservative
    defaults, so an unprobeable file is still attempted.
    """
    cmd = [
        FFPROBE, "-v", "error", "-show_entries",
        "stream=index,codec_type,codec_name,codec_tag_string,pix_fmt,bits_per_raw_sample,channels,"
        "color_range,color_space,color_transfer,color_primaries:stream_disposition=attached_pic",
        "-of", "json", str(path),
    ]  # fmt: skip
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, check=False).stdout
        streams = json.loads(out).get("streams", [])
    except (OSError, ValueError, AttributeError):
        return []
    return streams if isinstance(streams, list) else []


def _known(value: object) -> str | None:
    text = str(value or "").strip().lower()
    return None if text in _UNKNOWN else text


def is_deep_color(stream: dict[str, Any]) -> bool:
    """More than 8 bits per component, by raw sample depth or pixel format."""
    raw = str(stream.get("bits_per_raw_sample", ""))
    if raw.isdigit():
        return int(raw) > 8
    return bool(_DEEP_PIX_FMT.search(str(stream.get("pix_fmt", ""))))


def has_alpha(stream: dict[str, Any]) -> bool:
    return bool(_ALPHA_PIX_FMT.match(str(stream.get("pix_fmt", ""))))


def _is_attached_pic(stream: dict[str, Any]) -> bool:
    return bool((stream.get("disposition") or {}).get("attached_pic"))


def audio_bitrate_kbps(channels: int) -> int:
    """128k for mono/stereo, scaling with channel count for surround."""
    ch = min(max(channels, 1), MAX_AUDIO_CHANNELS)
    return max(MIN_AUDIO_KBPS, AUDIO_KBPS_PER_CHANNEL * ch)


def _video_args(video: dict[str, Any], n: int) -> tuple[list[str], list[str]]:
    """(per-stream encoder args, warnings) for output video stream `n`."""
    transfer = _known(video.get("color_transfer"))
    ten_bit = is_deep_color(video) or transfer in HDR_TRANSFERS
    args = [f"-pix_fmt:v:{n}", "yuv420p10le" if ten_bit else "yuv420p", f"-tag:v:{n}", "hvc1"]

    # Carry the source's colour description through so players interpret
    # HDR/wide-gamut/full-range footage correctly.
    for flag, key in (
        ("-color_primaries", "color_primaries"),
        ("-color_trc", "color_transfer"),
        ("-colorspace", "color_space"),
        ("-color_range", "color_range"),
    ):
        value = _known(video.get(key))
        if value:
            args += [f"{flag}:v:{n}", value]

    warnings = []
    if has_alpha(video):
        warnings.append(
            f"video stream {video.get('index')}: alpha channel isn't supported by HEVC .mp4 and was dropped"
        )
    return args, warnings


def _is_timecode(stream: dict[str, Any]) -> bool:
    return stream.get("codec_tag_string") == "tmcd"


def plan_streams(streams: list[dict[str, Any]], keep_extras: bool = True) -> StreamPlan:
    """Map, encode and tag every stream the .mp4 container can hold.

    Kept: every video stream (each re-encoded to HEVC, with its own pixel
    format and colour tags), all audio (AAC at a bitrate scaled to its
    channel count), text subtitles (converted to mov_text) and timecode
    (ffmpeg's muxer rebuilds the tmcd track from the ``timecode`` tag on the
    video streams, so the source tmcd track itself must *not* be mapped).

    Best-effort extras are stream-copied when `keep_extras` is true: cover
    art, bitmap subtitles and other data tracks. ffmpeg only finds out
    whether the container accepts them when it writes the header, so the
    caller retries with ``keep_extras=False`` if the first attempt fails, and
    the extras then show up in ``warnings`` as lost.
    """
    if not streams:
        return StreamPlan(
            args=[
                "-map",
                "0:v:0",
                "-map",
                "0:a?",
                "-pix_fmt",
                "yuv420p",
                "-tag:v",
                "hvc1",
                "-c:a",
                "aac",
                "-b:a",
                "128k",
            ]
        )

    videos: list[dict[str, Any]] = []
    covers: list[dict[str, Any]] = []
    audio: list[dict[str, Any]] = []
    subs: list[dict[str, Any]] = []
    data: list[dict[str, Any]] = []
    dropped: list[str] = []

    for s in streams:
        kind = s.get("codec_type")
        if kind == "video":
            (covers if _is_attached_pic(s) else videos).append(s)
        elif kind == "audio":
            audio.append(s)
        elif kind == "subtitle":
            subs.append(s)
        elif kind == "data":
            if not _is_timecode(s):
                data.append(s)
        else:
            dropped.append(f"stream {s.get('index')}: {kind} ({s.get('codec_name', '?')})")

    def is_text(s: dict[str, Any]) -> bool:
        return s.get("codec_name") in TEXT_SUBTITLE_CODECS

    def label(s: dict[str, Any]) -> str:
        return (
            f"{s.get('codec_type')} stream {s.get('index')} ({s.get('codec_name') or s.get('codec_tag_string') or '?'})"
        )

    args: list[str] = []
    warnings: list[str] = []
    extras: list[str] = []

    if not videos:
        warnings.append("no video stream found; output will contain audio only")
    for n, v in enumerate(videos):
        args += ["-map", f"0:{v['index']}"]
        v_args, v_warnings = _video_args(v, n)
        args += v_args
        warnings += v_warnings

    lost: list[dict[str, Any]] = []
    if covers:
        if keep_extras:
            for k, c in enumerate(covers):
                args += ["-map", f"0:{c['index']}", f"-c:v:{len(videos) + k}", "copy"]
            extras += [label(c) for c in covers]
        else:
            lost += covers

    for n, a in enumerate(audio):
        args += ["-map", f"0:{a['index']}"]
        channels = int(a["channels"]) if str(a.get("channels", "")).isdigit() else 2
        args += [f"-b:a:{n}", f"{audio_bitrate_kbps(channels)}k"]
    if audio:
        args += ["-c:a", "aac"]

    kept_subs = [x for x in subs if is_text(x) or keep_extras]
    lost += [x for x in subs if x not in kept_subs]
    for k, sub in enumerate(kept_subs):
        args += ["-map", f"0:{sub['index']}", f"-c:s:{k}", "mov_text" if is_text(sub) else "copy"]
        if not is_text(sub):
            extras.append(label(sub))

    if data:
        if keep_extras:
            for d in data:
                args += ["-map", f"0:{d['index']}"]
            args += ["-c:d", "copy"]
            extras += [label(d) for d in data]
        else:
            lost += data

    if lost:
        warnings.append("can't be stored in .mp4 and was dropped: " + ", ".join(label(x) for x in lost))

    return StreamPlan(args=args, warnings=warnings, extras=extras, dropped=dropped)
