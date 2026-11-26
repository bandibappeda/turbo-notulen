"""Operasi pada file lokal: ekstrak audio, pisah video, potong segmen."""
from __future__ import annotations

from pathlib import Path
from typing import Callable

from . import ffmpeg_tools as ff

StatusCB = Callable[[str], None]


def _cb(on_progress):
    return on_progress


def extract_audio(
    info: ff.MediaInfo,
    fmt: str,
    outdir: Path,
    bitrate: str | None = "192k",
    start: float | None = None,
    end: float | None = None,
    on_progress: ff.ProgressCB | None = None,
) -> Path:
    if not info.has_audio:
        raise ff.ToolError("File ini tidak memiliki track audio.")
    tag = _tag(start, end)
    dst = ff.output_audio_path(info, outdir, fmt, tag=tag)
    duration = _clip_duration(info, start, end)
    args = ff.build_audio_args(info, dst, fmt, bitrate, start, end)
    ff.run_ffmpeg(args, duration=duration, on_progress=on_progress)
    return dst


def extract_video(
    info: ff.MediaInfo,
    outdir: Path,
    reencode: bool = False,
    start: float | None = None,
    end: float | None = None,
    on_progress: ff.ProgressCB | None = None,
) -> Path:
    if not info.has_video:
        raise ff.ToolError("File ini tidak memiliki track video.")
    tag = _tag(start, end)
    dst = ff.output_video_path(info, outdir, tag=tag, reencode=reencode)
    duration = _clip_duration(info, start, end)
    args = ff.build_video_args(info, dst, reencode, start, end)
    ff.run_ffmpeg(args, duration=duration, on_progress=on_progress)
    return dst


def extract_both(
    info: ff.MediaInfo,
    outdir: Path,
    start: float | None = None,
    end: float | None = None,
    on_progress: ff.ProgressCB | None = None,
) -> Path:
    if not (info.has_video and info.has_audio):
        raise ff.ToolError("File ini tidak memiliki video dan audio sekaligus.")
    tag = _tag(start, end)
    dst = ff.output_av_path(info, outdir, tag=tag)
    duration = _clip_duration(info, start, end)
    args = ff.build_av_args(info, dst, start, end)
    ff.run_ffmpeg(args, duration=duration, on_progress=on_progress)
    return dst


def _tag(start: float | None, end: float | None) -> str:
    if start is None and end is None:
        return ""
    return f"{ff.timecode_filename(start or 0)} to {ff.timecode_filename(end)}"


def _clip_duration(info: ff.MediaInfo, start: float | None, end: float | None) -> float | None:
    if info.duration is None:
        return None
    begin = start or 0.0
    finish = end if end is not None else info.duration
    return max(0.0, min(finish, info.duration) - begin)
