"""Lokasi ffmpeg, pembacaan info media, dan operasi audio/video."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

IS_WIN = os.name == "nt"
_CREATE_NO_WINDOW = 0x08000000 if IS_WIN else 0
_EXE = ".exe" if IS_WIN else ""
APP_NAME = "splitmedia"

ProgressCB = Callable[[float, float | None], None]


class ToolError(RuntimeError):
    """ffmpeg tidak tersedia atau gagal dijalankan."""


# ---------------------------------------------------------------- locate ffmpeg

def _installer_bin_dir() -> Path:
    """Folder ffmpeg yang disiapkan installer (menyimpan di dalam proyek)."""
    return Path(__file__).resolve().parent / ".setup" / "bin"


def _legacy_bin_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_CACHE_HOME")
    if base:
        return Path(base) / APP_NAME / "bin"
    return Path.home() / ".cache" / APP_NAME / "bin"


def _cache_bin_dir() -> Path:
    """Lokasi penyimpanan ffmpeg bila tidak ada yang disiapkan installer."""
    return _installer_bin_dir() if _installer_bin_dir().parent.is_dir() else _legacy_bin_dir()


def _is_exe(path: Path) -> bool:
    return path.is_file() and os.access(path, os.X_OK)


def _find_on_path() -> str | None:
    return shutil.which("ffmpeg")


def _find_bundled() -> str | None:
    try:
        import imageio_ffmpeg
        exe = imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None
    return exe if os.path.isfile(exe) else None


def _link_or_copy(src: Path, dst: Path) -> None:
    if dst.exists():
        try:
            if dst.stat().st_size == src.stat().st_size:
                return
        except OSError:
            pass
        dst.unlink(missing_ok=True)
    try:
        os.link(src, dst)
        return
    except OSError:
        pass
    shutil.copy2(src, dst)


def _installer_ffmpeg() -> str | None:
    target = _installer_bin_dir() / f"ffmpeg{_EXE}"
    return str(target) if target.is_file() else None


def ensure_ffmpeg() -> str:
    """Pastikan ada `ffmpeg` bernama persis di PATH, kembalikan direktori bin-nya.

    yt-dlp mengecek keberadaan ffmpeg lewat PATH (bukan ffmpeg_location) untuk
    fitur unduh potongan, jadi kita siapkan satu direktori bin yang stabil.
    """
    explicit = os.environ.get("SPLITMEDIA_FFMPEG")
    if explicit and os.path.isfile(explicit):
        _prepend_path(str(Path(explicit).parent))
        return str(Path(explicit).parent)

    prepared = _installer_ffmpeg()
    if prepared:
        bin_dir = str(Path(prepared).parent)
        _prepend_path(bin_dir)
        os.environ["SPLITMEDIA_FFMPEG"] = prepared
        return bin_dir

    legacy = _legacy_bin_dir() / f"ffmpeg{_EXE}"
    source = str(legacy) if legacy.is_file() else None

    if source is None:
        on_path = _find_on_path()
        if on_path and Path(on_path).name.lower().startswith("ffmpeg"):
            bin_dir = str(Path(on_path).resolve().parent)
            _prepend_path(bin_dir)
            os.environ.setdefault("SPLITMEDIA_FFMPEG", on_path)
            return bin_dir
        source = _find_bundled()
        if source is None:
            raise ToolError(
                "FFmpeg belum siap. Tutup aplikasi lalu jalankan run.bat untuk "
                "menyiapkannya otomatis.\n"
                "Bila aplikasi dijalankan tanpa run.bat, pasang paket imageio-ffmpeg "
                "atau set SPLITMEDIA_FFMPEG ke path ffmpeg.exe."
            )

    src = Path(source).resolve()
    if src.name.lower() == f"ffmpeg{_EXE}":
        bin_dir = str(src.parent)
    else:
        bin_dir_path = _cache_bin_dir()
        bin_dir_path.mkdir(parents=True, exist_ok=True)
        target = bin_dir_path / f"ffmpeg{_EXE}"
        try:
            _link_or_copy(src, target)
        except OSError as exc:  # folder cache tidak bisa ditulis -> pakai path asli
            raise ToolError(f"Gagal menyiapkan ffmpeg di {bin_dir_path}: {exc}") from exc
        bin_dir = str(bin_dir_path)
    _prepend_path(bin_dir)
    os.environ["SPLITMEDIA_FFMPEG"] = str(Path(bin_dir) / f"ffmpeg{_EXE}")
    return bin_dir


def _prepend_path(directory: str) -> None:
    current = os.environ.get("PATH", "")
    parts = [p for p in current.split(os.pathsep) if p]
    if directory not in parts:
        os.environ["PATH"] = directory + (os.pathsep + current if current else "")


def ffmpeg_exe() -> str:
    env = os.environ.get("SPLITMEDIA_FFMPEG")
    if env and os.path.isfile(env):
        return env
    found = _find_on_path()
    if found:
        return found
    ensure_ffmpeg()
    return os.environ["SPLITMEDIA_FFMPEG"]


# ------------------------------------------------------------------ run process

def _popen(cmd: list[str], **kwargs) -> subprocess.Popen:
    return subprocess.Popen(
        cmd,
        stdin=subprocess.DEVNULL,
        stdout=kwargs.pop("stdout", subprocess.PIPE),
        stderr=kwargs.pop("stderr", subprocess.PIPE),
        text=True,
        errors="replace",
        encoding="utf-8",
        creationflags=_CREATE_NO_WINDOW,
        **kwargs,
    )


def _run_capture(cmd: list[str], timeout: float = 60) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        errors="replace",
        encoding="utf-8",
        timeout=timeout,
        creationflags=_CREATE_NO_WINDOW,
    )


def run_ffmpeg(
    args: Iterable[str],
    *,
    duration: float | None = None,
    on_progress: ProgressCB | None = None,
    log_path: Path | None = None,
) -> None:
    """Jalankan ffmpeg dengan progress berbasis waktu (out_time)."""
    cmd = [
        ffmpeg_exe(), "-hide_banner", "-nostdin", "-nostats", "-loglevel", "error",
        "-progress", "pipe:1", "-y", *args,
    ]
    proc = _popen(cmd)
    last_update = 0.0
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            line = line.strip()
            if line.startswith("out_time_us=") and on_progress:
                raw = line.split("=", 1)[1]
                if raw.isdigit():
                    now = time.monotonic()
                    if now - last_update >= 0.15:
                        on_progress(int(raw) / 1_000_000, duration)
                        last_update = now
            elif line.startswith("out_time=") and on_progress and duration is None:
                secs = _parse_clock(line.split("=", 1)[1])
                if secs is not None:
                    on_progress(secs, None)
        stderr = proc.stderr.read() if proc.stderr else ""
        code = proc.wait()
    finally:
        if proc.poll() is None:
            proc.kill()
    if code != 0:
        detail = (stderr or "").strip() or f"ffmpeg keluar dengan kode {code}"
        raise ToolError(detail.splitlines()[-1] if detail else "ffmpeg gagal")
    if on_progress and duration:
        on_progress(duration, duration)


def _parse_clock(text: str) -> float | None:
    text = text.strip()
    if not text or text.startswith("N/A"):
        return None
    try:
        parts = [float(p) for p in text.split(":")]
    except ValueError:
        return None
    if len(parts) == 3:
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    if len(parts) == 2:
        return parts[0] * 60 + parts[1]
    return parts[0] if parts else None


# -------------------------------------------------------------------- media info

@dataclass
class Stream:
    index: int
    kind: str  # video | audio | image
    codec: str
    detail: str
    width: int | None = None
    height: int | None = None
    channels: int | None = None
    sample_rate: int | None = None

    @property
    def label(self) -> str:
        if self.kind == "audio":
            bits = [self.codec]
            if self.sample_rate:
                bits.append(f"{self.sample_rate} Hz")
            if self.channels:
                bits.append(f"{self.channels} ch")
            return " · ".join(bits)
        if self.kind == "video":
            size = f"{self.width}x{self.height}" if self.width and self.height else "?"
            return f"{self.codec} · {size}"
        return self.codec


@dataclass
class MediaInfo:
    path: Path
    duration: float | None
    streams: list[Stream] = field(default_factory=list)

    @property
    def video(self) -> Stream | None:
        return next((s for s in self.streams if s.kind == "video"), None)

    @property
    def audio(self) -> Stream | None:
        return next((s for s in self.streams if s.kind == "audio"), None)

    @property
    def has_video(self) -> bool:
        return self.video is not None

    @property
    def has_audio(self) -> bool:
        return self.audio is not None

    @property
    def duration_text(self) -> str:
        return format_seconds(self.duration) if self.duration else "?"


_DURATION_RE = re.compile(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)")
_STREAM_RE = re.compile(r"Stream #\d+:\d+(?:\[[^\]]+\])?(?:\([^)]*\))?:\s*(Video|Audio|Subtitle|Data|Attachment):\s*(.*)")
_SIZE_RE = re.compile(r"(\d{2,5})x(\d{2,5})")
_CHANNELS_RE = re.compile(r"(\d+)\s+channels?")
_RATE_RE = re.compile(r"(\d+)\s+Hz")
_ATTACHED_RE = re.compile(r"\(attached pic\)|attached_pic", re.IGNORECASE)


def probe(path: str | Path) -> MediaInfo:
    path = Path(path)
    if not path.is_file():
        raise ToolError(f"File tidak ditemukan: {path}")
    result = _run_capture([ffmpeg_exe(), "-hide_banner", "-i", str(path)])
    text = result.stderr or ""
    if "No such file" in text or "Invalid data found" in text:
        raise ToolError(f"Bukan file media yang bisa dibaca: {path.name}")
    duration = None
    m = _DURATION_RE.search(text)
    if m:
        duration = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    streams: list[Stream] = []
    for line in text.splitlines():
        m = _STREAM_RE.search(line)
        if not m:
            continue
        kind_raw = m.group(1).lower()
        rest = m.group(2).strip()
        codec = rest.split(",", 1)[0].split()[0] if rest else "?"
        stream = Stream(index=len(streams), kind=kind_raw, codec=codec, detail=rest)
        if kind_raw == "video":
            if _ATTACHED_RE.search(line):
                stream.kind = "image"
            sm = _SIZE_RE.search(rest)
            if sm:
                stream.width, stream.height = int(sm.group(1)), int(sm.group(2))
        elif kind_raw == "audio":
            cm = _CHANNELS_RE.search(rest)
            if cm:
                stream.channels = int(cm.group(1))
            rm = _RATE_RE.search(rest)
            if rm:
                stream.sample_rate = int(rm.group(1))
        elif kind_raw != "audio":
            continue
        streams.append(stream)
    return MediaInfo(path=path, duration=duration, streams=streams)


def timecode_filename(seconds: float | None) -> str:
    """Titik waktu aman untuk nama file: 00-01-30 atau 01-30.50."""
    if seconds is None:
        return "end"
    return timecode(seconds).replace(":", "-")


def timecode(seconds: float | None) -> str:
    """HH:MM:SS dengan milidetik hanya bila perlu."""
    if seconds is None:
        return "?"
    seconds = max(0.0, float(seconds))
    h = int(seconds // 3600)
    m = int(seconds % 3600 // 60)
    s = seconds % 60
    if abs(s - round(s)) < 0.05:
        return f"{h:02d}:{m:02d}:{round(s):02d}"
    return f"{h:02d}:{m:02d}:{s:05.2f}"


def format_seconds(seconds: float | None) -> str:
    if seconds is None:
        return "?"
    seconds = max(0.0, float(seconds))
    h = int(seconds // 3600)
    m = int(seconds % 3600 // 60)
    s = seconds % 60
    if h:
        return f"{h}:{m:02d}:{s:05.2f}"
    return f"{m}:{s:05.2f}"


# ------------------------------------------------------------------- conversions

AUDIO_TARGETS = {
    "mp3": {"ext": "mp3", "codec": "libmp3lame", "bitrate": True},
    "m4a": {"ext": "m4a", "codec": "aac", "bitrate": True},
    "opus": {"ext": "opus", "codec": "libopus", "bitrate": True},
    "wav": {"ext": "wav", "codec": "pcm_s16le", "bitrate": False},
    "flac": {"ext": "flac", "codec": "flac", "bitrate": False},
}

_CONTAINER_FOR_CODEC = {
    "aac": "m4a", "mp3": "mp3", "opus": "opus", "vorbis": "ogg", "flac": "flac",
    "alac": "m4a", "ac3": "ac3", "eac3": "eac3", "pcm_s16le": "wav",
}

_MP4_VIDEO_CODECS = {"h264", "hevc", "av1", "mpeg4", "mpeg2video"}


def safe_output(path: Path) -> Path:
    """Hindari menimpa file yang sudah ada: tambah (2), (3), ..."""
    if not path.exists():
        return path
    stem, suffix = path.stem, path.suffix
    for n in range(2, 1000):
        candidate = path.with_name(f"{stem} ({n}){suffix}")
        if not candidate.exists():
            return candidate
    return path.with_name(f"{stem} ({int(time.time())}){suffix}")


def build_audio_args(
    info: MediaInfo,
    dst: Path,
    fmt: str,
    bitrate: str | None = None,
    start: float | None = None,
    end: float | None = None,
) -> list[str]:
    target = AUDIO_TARGETS.get(fmt)
    if target is None:
        raise ToolError(f"Format audio tidak dikenal: {fmt}")
    args: list[str] = []
    if start is not None:
        args += ["-ss", f"{start:.3f}"]
    if end is not None:
        args += ["-to", f"{end:.3f}"]
    args += ["-i", str(info.path), "-map", "0:a:0", "-vn"]
    codec = target["codec"]
    bitrate = bitrate if target["bitrate"] else None
    args += ["-c:a", codec]
    if bitrate:
        args += ["-b:a", bitrate]
    if fmt == "m4a":
        args += ["-movflags", "+faststart"]
    if fmt == "mp3" and bitrate == "320k":
        args += ["-q:a", "0"]
    args += [str(dst)]
    return args


def build_video_args(
    info: MediaInfo,
    dst: Path,
    reencode: bool = False,
    start: float | None = None,
    end: float | None = None,
) -> list[str]:
    args: list[str] = []
    if start is not None:
        args += ["-ss", f"{start:.3f}"]
    if end is not None:
        args += ["-to", f"{end:.3f}"]
    args += ["-i", str(info.path), "-map", "0:v:0", "-an"]
    if reencode:
        args += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p"]
    else:
        args += ["-c:v", "copy"]
    if dst.suffix.lower() in {".mp4", ".m4v"} and not reencode:
        args += ["-movflags", "+faststart"]
    args += [str(dst)]
    return args


def build_av_args(
    info: MediaInfo,
    dst: Path,
    start: float | None = None,
    end: float | None = None,
) -> list[str]:
    args: list[str] = []
    if start is not None:
        args += ["-ss", f"{start:.3f}"]
    if end is not None:
        args += ["-to", f"{end:.3f}"]
    args += ["-i", str(info.path), "-map", "0:v:0", "-map", "0:a:0?", "-c", "copy"]
    args += [str(dst)]
    return args


def _tagged(stem: str, tag: str) -> str:
    return f"{stem} ({tag})" if tag else stem


def output_audio_path(info: MediaInfo, outdir: Path, fmt: str, tag: str = "") -> Path:
    ext = AUDIO_TARGETS[fmt]["ext"]
    return safe_output(outdir / f"{_tagged(info.path.stem + ' - audio', tag)}.{ext}")


def output_video_path(info: MediaInfo, outdir: Path, tag: str = "", reencode: bool = False) -> Path:
    video = info.video
    ext = "mp4"
    if video and not reencode and video.codec not in _MP4_VIDEO_CODECS:
        ext = "mkv"
    return safe_output(outdir / f"{_tagged(info.path.stem + ' - video', tag)}.{ext}")


def output_av_path(info: MediaInfo, outdir: Path, tag: str = "") -> Path:
    return safe_output(outdir / f"{_tagged(info.path.stem, tag)}.mp4")


# ---------------------------------------------------------------- split helper

def audio_copy_spec(stream: Stream | None) -> tuple[str, list[str]]:
    """Pilih backend -c:a untuk split.

    Codec ber-frame tetap (PCM) tidak bisa dipotong presisi via stream copy —
    ffmpeg membulatkan ke batas frame sehingga potongan bisa meleset ±1 frame.
    Untuk codec itu dipakai encoder lossless-nya sendiri supaya durasi tepat,
    tanpa kehilangan kualitas. Codec ber-frame besar tetap disalin agar cepat.
    """
    codec = (stream.codec if stream else "").lower().split(".")[0]
    if codec.startswith("pcm_"):
        return codec, []
    return "copy", []


def audio_stream_ext(stream: Stream | None) -> str:
    """Perkiraan ekstensi terbaik agar audio bisa disalin tanpa encode ulang."""
    codec = (stream.codec if stream else "").lower().split(".")[0]
    for known, ext in sorted(_CONTAINER_FOR_CODEC.items(), key=lambda kv: -len(kv[0])):
        if codec.startswith(known):
            return ext
    if codec.startswith("mp4a"):
        return "m4a"
    return "mka"


def split_audio_ranges(
    info: MediaInfo,
    outdir: Path,
    ranges: list[tuple[float | None, float | None]],
    *,
    index_prefix: str = "bagian",
    on_progress: ProgressCB | None = None,
    on_part: Callable[["Path", int, int], None] | None = None,
    cancel: Callable[[], bool] | None = None,
) -> list[Path]:
    """Potong track audio menjadi beberapa file tanpa re-encode.

    Setiap rentang (start, end) menghasilkan satu file; end=None berarti sampai
    akhir track. Segmen diproses satu per satu karena ffmpeg bisa memakai ulang
    koneksi input yang sama untuk banyak output, tetapi urutan-urutan progress
    per segmen lebih mudah dipetakan bila dijalankan terpisah.
    """
    if not info.has_audio:
        raise ToolError("File ini tidak memiliki track audio.")

    ext = audio_stream_ext(info.audio)
    total = len(ranges)
    outputs: list[Path] = []
    duration = info.duration

    # Nama file dibuat dulu semua, agar penomoran "(2)" akibat file lama tidak
    # saling bertabrakan ketika beberapa bagian punya nama yang sama persis.
    planned: list[tuple[Path, float, float | None]] = []
    for part_no, (start, end) in enumerate(ranges, start=1):
        label = (
            f"{timecode_filename(start or 0)} to {timecode_filename(end)}"
            if end is not None
            else f"{timecode_filename(start or 0)} to akhir"
        )
        dst = safe_output(
            outdir / f"{info.path.stem} - {index_prefix} {part_no:02d} ({label}).{ext}"
        )
        seg_start = max(0.0, start or 0.0)
        seg_end = end
        if duration:
            seg_end = min(end if end is not None else duration, duration)
        planned.append((dst, seg_start, seg_end))
        if on_part is not None:
            on_part(dst, part_no, total)

    for part_no, (dst, seg_start, seg_end) in enumerate(planned, start=1):
        if cancel and cancel():
            break
        seg_len = None
        if seg_end is not None:
            seg_len = max(0.0, seg_end - seg_start)
        codec, codec_opts = audio_copy_spec(info.audio)
        args: list[str] = []
        args += ["-ss", f"{seg_start:.3f}"]
        if seg_end is not None:
            args += ["-to", f"{seg_end:.3f}"]
        # CATATAN: jangan pakai -avoid_negative_ts di sini. Opsi itu membuat
        # ffmpeg menggeser timestamp sehingga potongan AAC di MP4 ikut memuat
        # frame setelah titik akhir (bagian 30 detik bisa jadi 43 detik).
        args += [
            "-i", str(info.path),
            "-map", "0:a:0",
            "-vn", "-c:a", codec, *codec_opts,
            "-map_metadata", "0",
        ]
        if ext == "mp3":
            args += ["-id3v2_version", "3"]
        args.append(str(dst))
        run_ffmpeg(args, duration=seg_len, on_progress=on_progress)
        outputs.append(dst)

    return outputs


def split_audio_every(
    info: MediaInfo,
    outdir: Path,
    chunk: float,
    *,
    start: float | None = None,
    end: float | None = None,
    on_progress: ProgressCB | None = None,
    on_part: Callable[["Path", int, int], None] | None = None,
    cancel: Callable[[], bool] | None = None,
) -> list[Path]:
    """Bagi audio menjadi bagian berdurasi tetap (mis. setiap 00:29:59)."""
    if chunk <= 0:
        raise ToolError("Durasi bagian harus lebih besar dari nol.")
    if info.duration is None:
        raise ToolError("Durasi audio tidak diketahui, tidak bisa dibagi otomatis.")
    begin = max(0.0, start or 0.0)
    finish = min(end if end is not None else info.duration, info.duration)
    if finish <= begin:
        raise ToolError("Rentang yang dipilih kosong.")
    ranges = make_ranges(begin, finish, chunk)
    return split_audio_ranges(
        info, outdir, ranges,
        on_progress=on_progress, on_part=on_part, cancel=cancel,
    )


def make_ranges(begin: float, finish: float, chunk: float) -> list[tuple[float, float]]:
    ranges: list[tuple[float, float]] = []
    cursor = begin
    while cursor < finish - 0.001:
        stop = min(cursor + chunk, finish)
        ranges.append((cursor, stop))
        cursor = stop
    return ranges


def make_ranges_from_points(begin: float, finish: float, points: list[float]) -> list[tuple[float, float]]:
    """Pecah [begin, finish] pada titik-titik potong yang diberikan."""
    cuts = sorted({p for p in points if begin < p < finish})
    edges = [begin, *cuts, finish]
    return [(edges[i], edges[i + 1]) for i in range(len(edges) - 1)]
