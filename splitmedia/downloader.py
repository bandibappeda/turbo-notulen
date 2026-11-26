"""Unduhan via yt-dlp: audio saja, video saja, atau potongan segmen."""
from __future__ import annotations

import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError, ExtractorError

from . import ffmpeg_tools as ff

ProgressCB = Callable[[dict], None]


class DownloadFailed(RuntimeError):
    pass


class UrlNotSupported(DownloadFailed):
    pass


URL_RE = re.compile(r"^https?://", re.IGNORECASE)

VIDEO_FORMAT = "bv*[vcodec^=avc1]/bv*[ext=mp4]/bv*"
AUDIO_FORMAT = "ba/b"
AV_FORMAT = "bv*[vcodec^=avc1]+ba[acodec^=mp4a]/bv*+ba/b"


@dataclass
class DownloadRequest:
    url: str
    mode: str  # "audio" | "video" | "both" | "separate"
    outdir: Path
    start: float | None = None
    end: float | None = None
    audio_format: str = "mp3"
    audio_bitrate: str | None = "192k"


def _clean(message: str) -> str:
    return re.sub(r"^ERROR:\s*", "", (message or "").strip()) or "Terjadi kesalahan yt-dlp"


def _sanitize(name: str) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")
    return name[:150] or "media"


def check_url(url: str) -> None:
    if not URL_RE.match(url.strip()):
        raise UrlNotSupported("URL harus diawali http:// atau https://")


# ------------------------------------------------------------------ yt-dlp setup

class _QuietLogger:
    """yt-dlp tidak perlu mencetak apa pun; semua pesan kita tampilkan sendiri."""

    def debug(self, msg: str) -> None:
        pass

    def info(self, msg: str) -> None:
        pass

    def warning(self, msg: str) -> None:
        pass

    def error(self, msg: str) -> None:
        pass


def _opts(bin_dir: str, workdir: Path, progress: ProgressCB | None, download: bool) -> dict:
    opts = {
        "logger": _QuietLogger(),
        "ffmpeg_location": bin_dir,
        "outtmpl": os.path.join(str(workdir), "%(title).120B [%(id)s].%(ext)s"),
        "paths": {"home": str(workdir)},
        "noplaylist": True,
        "overwrites": True,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "consoletitle": False,
        "windowsfilenames": True,
        "retries": 5,
        "fragment_retries": 5,
        "socket_timeout": 30,
        "trim_file_name": 180,
    }
    if not download:
        opts["skip_download"] = True
    if progress is not None:
        opts["progress_hooks"] = [_progress_hook(progress)]
        opts["postprocessor_hooks"] = [_pp_hook(progress)]
    return opts


def _progress_hook(cb: ProgressCB) -> Callable[[dict], None]:
    def hook(d: dict) -> None:
        status = d.get("status")
        info = d.get("info_dict") or {}
        if status == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            cb({
                "event": "download",
                "done": d.get("downloaded_bytes") or 0,
                "total": total,
                "speed": d.get("speed"),
                "eta": d.get("eta"),
                "title": info.get("title"),
                "duration": info.get("duration"),
            })
        elif status == "finished":
            cb({"event": "download_finished"})
    return hook


def _pp_hook(cb: ProgressCB) -> Callable[[dict], None]:
    def hook(d: dict) -> None:
        if d.get("status") == "started":
            cb({"event": "postprocess", "name": d.get("postprocessor") or "ffmpeg"})
    return hook


def _run_extract(opts: dict, url: str, download: bool) -> dict:
    with YoutubeDL(opts) as ydl:
        try:
            info = ydl.extract_info(url, download=download)
        except ExtractorError as exc:
            raise DownloadFailed(_clean(str(exc))) from exc
        except DownloadError as exc:
            raise DownloadFailed(_clean(str(exc))) from exc
    if not info:
        raise DownloadFailed("Tidak ada data yang bisa diambil dari URL ini.")
    if info.get("_type") == "playlist":
        entries = [e for e in info.get("entries") or [] if e]
        if not entries:
            raise DownloadFailed("URL ini playlist kosong; berikan URL satu video.")
        info = entries[0]
    return info


def probe_url(url: str, bin_dir: str, progress: ProgressCB | None = None) -> dict:
    """Ambil judul/durasi/format tanpa mengunduh."""
    check_url(url)
    workdir = Path(tempfile.mkdtemp(prefix="splitmedia-info-"))
    try:
        return _run_extract(_opts(bin_dir, workdir, progress, download=False), url, download=False)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


# -------------------------------------------------------------------- download

def download(rq: DownloadRequest, bin_dir: str, progress: ProgressCB | None = None) -> list[Path]:
    check_url(rq.url)
    rq.outdir.mkdir(parents=True, exist_ok=True)
    workdir = Path(tempfile.mkdtemp(prefix="splitmedia-dl-"))
    try:
        opts = _opts(bin_dir, workdir, progress, download=True)
        clipped = rq.start is not None or rq.end is not None
        if clipped:
            opts["download_ranges"] = lambda _info, _ydl, _rq=rq: [_range(_rq)]
            opts["force_keyframes_at_cuts"] = True
        if rq.mode == "audio":
            opts["format"] = AUDIO_FORMAT
        elif rq.mode == "video":
            opts["format"] = VIDEO_FORMAT
        else:
            # "both" (satu file gabungan) dan "separate" (dipecah jadi dua file)
            # sama-sama mengunduh video+audio terbaik lalu digabung dulu
            opts["format"] = AV_FORMAT
            opts["merge_output_format"] = "mp4"

        info = _run_extract(opts, rq.url, download=True)
        sources = _collect_outputs(info)
        if not sources:
            raise DownloadFailed("Unduhan selesai tetapi file hasil tidak ditemukan.")
        src = sources[0]
        if rq.mode == "separate":
            return _finalize_separate(src, info, rq, progress)
        return [_finalize(src, info, rq, progress)]
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def _range(rq: DownloadRequest) -> dict:
    rng: dict = {}
    if rq.start is not None:
        rng["start_time"] = float(rq.start)
    if rq.end is not None:
        rng["end_time"] = float(rq.end)
    return rng


def _collect_outputs(info: dict) -> list[Path]:
    found: list[Path] = []
    for entry in info.get("requested_downloads") or []:
        for key in ("filepath", "_filename"):
            fp = entry.get(key)
            if fp and os.path.isfile(fp):
                found.append(Path(fp))
                break
    if not found:
        for key in ("filepath", "_filename"):
            fp = info.get(key)
            if fp and os.path.isfile(fp):
                found.append(Path(fp))
    unique: list[Path] = []
    for p in found:
        if p not in unique:
            unique.append(p)
    return unique


def _clip_tag(rq: DownloadRequest) -> str:
    if rq.start is None and rq.end is None:
        return ""
    return f" ({ff.timecode_filename(rq.start or 0)} to {ff.timecode_filename(rq.end)})"


def _finalize(src: Path, info: dict, rq: DownloadRequest, progress: ProgressCB | None) -> Path:
    title = _sanitize(info.get("title") or "media")
    rid = _sanitize(str(info.get("id") or "media"))[:40]
    tag = _clip_tag(rq)
    stem = f"{title} [{rid}]{tag}"

    if rq.mode == "audio":
        ext = ff.AUDIO_TARGETS[rq.audio_format]["ext"]
        dst = ff.safe_output(rq.outdir / f"{stem} - audio.{ext}")
        if progress:
            progress({"event": "convert_audio", "path": dst})
        meta = ff.probe(src)
        args = ff.build_audio_args(meta, dst, rq.audio_format, rq.audio_bitrate)
        ff.run_ffmpeg(args, duration=meta.duration, on_progress=_ff_cb(progress, meta.duration))
        return dst

    if rq.mode == "video":
        if src.suffix.lower() in {".mp4", ".m4v"}:
            dst = ff.safe_output(rq.outdir / f"{stem} - video.mp4")
            shutil.move(str(src), str(dst))
            return dst
        # codec non-MP4 (mis. VP9/Opus) tetap valid di MKV, jadi cukup di-remux tanpa re-encode
        dst = ff.safe_output(rq.outdir / f"{stem} - video.mkv")
        if progress:
            progress({"event": "remux", "path": dst})
        ff.run_ffmpeg(["-i", str(src), "-map", "0:v:0", "-an", "-c:v", "copy", str(dst)])
        return dst

    dst = ff.safe_output(rq.outdir / f"{stem}.mp4")
    if src.suffix.lower() == ".mp4":
        shutil.move(str(src), str(dst))
        return dst
    if progress:
        progress({"event": "remux", "path": dst})
    ff.run_ffmpeg(["-i", str(src), "-map", "0:v:0", "-map", "0:a:0?", "-c", "copy", str(dst)])
    return dst


def _finalize_separate(
    src: Path, info: dict, rq: DownloadRequest, progress: ProgressCB | None
) -> list[Path]:
    """Pecah hasil unduhan menjadi satu file audio dan satu file video tanpa suara."""
    stem = f"{_sanitize(info.get('title') or 'media')} [{_sanitize(str(info.get('id') or 'media'))[:40]}]{_clip_tag(rq)}"
    meta = ff.probe(src)
    outputs: list[Path] = []

    if meta.has_audio:
        ext = ff.AUDIO_TARGETS[rq.audio_format]["ext"]
        audio_dst = ff.safe_output(rq.outdir / f"{stem} - audio.{ext}")
        if progress:
            progress({"event": "convert_audio", "path": audio_dst})
        args = ff.build_audio_args(meta, audio_dst, rq.audio_format, rq.audio_bitrate)
        ff.run_ffmpeg(args, duration=meta.duration, on_progress=_ff_cb(progress, meta.duration))
        outputs.append(audio_dst)

    if meta.has_video:
        container = "mp4" if (meta.video and meta.video.codec in _MP4_VIDEO_CODECS) else "mkv"
        video_dst = ff.safe_output(rq.outdir / f"{stem} - video.{container}")
        if progress:
            progress({"event": "remux", "path": video_dst})
        args = ["-i", str(src), "-map", "0:v:0", "-an", "-c:v", "copy"]
        if container == "mp4":
            args += ["-movflags", "+faststart"]
        args.append(str(video_dst))
        ff.run_ffmpeg(args, duration=meta.duration, on_progress=_ff_cb(progress, meta.duration))
        outputs.append(video_dst)

    if not outputs:
        raise DownloadFailed("Hasil unduhan tidak memiliki track audio maupun video.")
    return outputs


_MP4_VIDEO_CODECS = {"h264", "hevc", "av1", "mpeg4", "mpeg2video"}


def _ff_cb(progress: ProgressCB | None, duration: float | None):
    if progress is None:
        return None

    def cb(done: float, total: float | None) -> None:
        progress({"event": "ffmpeg", "done": done, "total": total or duration})

    return cb


def format_table(info: dict, limit: int = 12) -> list[tuple[str, str, str, str]]:
    """Ringkas daftar format yang tersedia."""
    rows: list[tuple[str, str, str, str]] = []
    for f in info.get("formats") or []:
        vcodec = f.get("vcodec") or "none"
        acodec = f.get("acodec") or "none"
        if vcodec == "none" and acodec == "none":
            continue
        kind = "video" if vcodec != "none" else "audio"
        res = f.get("resolution") or "audio"
        if kind == "audio":
            abr = f.get("abr")
            res = f"{abr:.0f}k" if abr else "?"
        rows.append((str(f.get("format_id", "?")), kind, res, f.get("ext", "?")))
    rows.reverse()
    return rows[:limit]
