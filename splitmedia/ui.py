"""Antarmuka terminal: menu, prompt input, dan tampilan progres."""
from __future__ import annotations

import os
import re
import shutil
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from rich import box
from rich.align import Align
from rich.console import Console, Group
from rich.markup import escape
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    DownloadColumn,
    Progress,
    ProgressColumn,
    SpinnerColumn,
    TaskID,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
    TransferSpeedColumn,
)
from rich.table import Table
from rich.text import Text

from . import downloader as dl
from . import ffmpeg_tools as ff
from . import operations as ops
from . import textmerge as tx

console = Console(highlight=False)

AUDIO_CHOICES: list[tuple[str, str, str, str]] = [
    ("mp3", "128k", "MP3 128 kbps", "kecil, cukup untuk podcast"),
    ("mp3", "192k", "MP3 192 kbps", "seimbang (disarankan)"),
    ("mp3", "320k", "MP3 320 kbps", "kualitas tertinggi"),
    ("m4a", "192k", "M4A/AAC 192 kbps", "efisien, cocok untuk Apple"),
    ("opus", "160k", "Opus 160 kbps", "paling efisien untuk suara"),
    ("wav", "-", "WAV", "tanpa kompresi (besar)"),
    ("flac", "-", "FLAC", "lossless terkompresi"),
]


# ====================================================================== tampilan

def banner() -> None:
    title = Text("splitmedia", style="bold cyan")
    sub = Text("Download · pisah audio/video · potong segmen", style="dim")
    console.print(Panel(Align.center(Group(title, sub)), box=box.ROUNDED, border_style="cyan", padding=(0, 4)))


def tool_line(bin_dir: str) -> None:
    try:
        import yt_dlp
        ver = yt_dlp.version.__version__
    except Exception:
        ver = "?"
    ff_path = os.environ.get("SPLITMEDIA_FFMPEG") or ff.ffmpeg_exe()
    style = "green" if os.path.isfile(ff_path) else "red"
    line = Text()
    line.append("yt-dlp ", style="dim")
    line.append(ver)
    line.append("   ffmpeg ", style="dim")
    line.append(ff_path, style=style)
    console.print(line, justify="center")
    console.print()


def section(text: str) -> None:
    console.print(f"\n[bold cyan]▸ {text}[/]")
    flush()


def note(text: str) -> None:
    console.print(f"  [dim]{escape(str(text))}[/]")


def success(text: str) -> None:
    console.print(f"  [green]✔[/] {escape(str(text))}")


def warn(text: str) -> None:
    console.print(f"  [yellow]![/] {escape(_one_line(text))}")


def _one_line(text: str) -> str:
    """Rapikan pesan error multi-baris agar tetap terbaca di dalam indentasi."""
    plain = str(text).strip()
    lines = [ln.strip() for ln in plain.splitlines() if ln.strip()]
    return "\n  ".join(lines) if lines else plain


def error(text: str) -> None:
    console.print(f"  [red]✘[/] {escape(_one_line(text))}")


def hint(text: str) -> None:
    console.print(f"  [dim italic]{escape(str(text))}[/dim italic]")


# ======================================================================== input

def _input(prompt: str) -> str:
    try:
        console.file.flush()
        return console.input(prompt).strip()
    except (EOFError, KeyboardInterrupt):
        console.print()
        raise SystemExit(0) from None


def pause() -> None:
    _input("  [dim]Tekan Enter untuk melanjutkan…[/]")


def menu(options: list[tuple[str, str]], *, title: str = "Menu") -> str:
    """Menu bernomor; kembalikan kunci opsi. '0' selalu berarti kembali."""
    section(title)
    for key, label in options:
        console.print(f"  [bold cyan]{key}[/]  {label}")
    console.print("  [bold cyan]0[/]  [dim]Kembali[/]")
    keys = {k for k, _ in options}
    while True:
        raw = _input("  [bold]›[/] ")
        if raw in keys or raw == "0":
            return raw
        if raw == "":
            continue
        error("Pilihan tidak tersedia.")


def ask(prompt: str, default: str | None = None) -> str:
    suffix = f" [dim]({default})[/]" if default else ""
    raw = _input(f"  [bold]{prompt}[/]{suffix} [bold]›[/] ")
    if not raw and default is not None:
        return default
    return raw


def _folder_from_input(raw: str) -> Path:
    p = Path(os.path.expandvars(os.path.expanduser(raw)))
    # di Windows, "D:folder" berarti relatif terhadap folder kerja drive D,
    # padahal pengguna hampir selalu bermaksud "D:\\folder"
    if p.drive and not p.root and str(p.drive) == str(p)[:2] and p.anchor == str(p.drive):
        p = Path(f"{p.drive}\\{str(p)[2:].lstrip('\\/')}")
    return p


def ask_path(prompt: str) -> Path | None:
    raw = ask(prompt)
    if not raw:
        return None
    raw = raw.strip().strip('"').strip("'")
    p = _folder_from_input(raw)
    if not p.exists():
        error(f"Tidak ditemukan: {p}")
        return None
    return p


def ask_outdir(default: Path | None = None) -> Path:
    default = default or Path.cwd()
    raw = ask("Folder output", str(default))
    p = _folder_from_input(raw.strip().strip('"').strip("'"))
    try:
        p.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        error(f"Tidak bisa membuat folder: {exc}")
        return default
    return p


def ask_audio_profile() -> tuple[str, str | None]:
    section("Format audio")
    for i, (fmt, br, label, desc) in enumerate(AUDIO_CHOICES, 1):
        console.print(f"  [bold cyan]{i}[/]  {label:<18} [dim]{desc}[/]")
    while True:
        raw = _input("  [bold]›[/] [dim](2)[/] ") or "2"
        if raw.isdigit() and 1 <= int(raw) <= len(AUDIO_CHOICES):
            fmt, br, *_ = AUDIO_CHOICES[int(raw) - 1]
            return fmt, (None if br == "-" else br)
        error("Pilih nomor yang tersedia.")


_TIME_RE = re.compile(r"^(?:(\d+):)?(\d{1,2}):(\d{1,2}(?:\.\d+)?)$")
_SECONDS_RE = re.compile(r"^\d+(?:\.\d+)?$")


def parse_time(text: str) -> float | None:
    """Terima `HH:MM:SS`, `MM:SS`, atau jumlah detik (`90`, `90.5`)."""
    text = text.strip()
    if not text:
        return None
    if _SECONDS_RE.match(text):
        return float(text)
    m = _TIME_RE.match(text)
    if not m:
        raise ValueError(text)
    hours = int(m.group(1) or 0)
    minutes = int(m.group(2))
    seconds = float(m.group(3))
    if minutes > 59 or seconds >= 60:
        raise ValueError(text)
    return hours * 3600 + minutes * 60 + seconds


@dataclass
class Segment:
    start: float | None = None
    end: float | None = None

    @property
    def is_full(self) -> bool:
        return self.start is None and self.end is None

    def describe(self, total: float | None = None) -> str:
        if self.is_full:
            return "seluruh durasi"
        s = ff.format_seconds(self.start or 0)
        e = ff.format_seconds(self.end)
        return f"{s} → {e}"


def ask_segment(duration: float | None = None) -> Segment:
    section("Rentang waktu")
    note("Contoh: 30-90  ·  00:30-01:30  ·  1:02:03-1:05:00")
    note("Kosongkan untuk mengambil seluruh durasi.")
    if duration:
        note(f"Durasi media: {ff.format_seconds(duration)}")
    while True:
        raw = _input("  [bold]Rentang[/] [dim](kosong = semua)[/] [bold]›[/] ")
        if not raw:
            return Segment()
        separator = "-" if "-" in raw else None
        if not separator:
            error("Gunakan format mulai-selesai, mis. 30-90.")
            continue
        left, _, right = raw.partition("-")
        try:
            start = parse_time(left) if left.strip() else None
            end = parse_time(right) if right.strip() else None
        except ValueError:
            error("Waktu tidak valid. Gunakan MM:SS atau HH:MM:SS.")
            continue
        if start is None and end is None:
            return Segment()
        if start is not None and end is not None and end <= start:
            error("Waktu selesai harus lebih besar dari waktu mulai.")
            continue
        if duration and start is not None and start >= duration:
            error(f"Waktu mulai melewati durasi media ({ff.format_seconds(duration)}).")
            continue
        if duration and end is None and start is not None:
            end = duration
        return Segment(start=start, end=end)


def ask_cut_points(duration: float | None) -> list[float]:
    """Minta daftar titik potong, mis. `00:10:00 00:29:59`."""
    section("Titik potong")
    note("Ketik satu atau beberapa waktu, pisahkan dengan spasi atau koma.")
    note("Contoh: 00:29:59  ·  00:10:00, 01:02:03  ·  MM:SS juga bisa")
    if duration:
        note(f"Durasi audio: {ff.format_seconds(duration)}")
    while True:
        raw = _input("  [bold]Titik potong[/] [bold]›[/] ")
        if not raw:
            error("Isi minimal satu titik potong.")
            continue
        parts = [p for p in re.split(r"[\s,;]+", raw) if p]
        points: list[float] = []
        bad: list[str] = []
        for part in parts:
            try:
                value = parse_time(part)
            except ValueError:
                bad.append(part)
                continue
            if value is None:
                continue
            points.append(value)
        if bad:
            error(f"Waktu tidak valid: {', '.join(bad)}. Gunakan MM:SS atau HH:MM:SS.")
            continue
        points = sorted(set(points))
        if duration:
            over = [p for p in points if p >= duration]
            if over:
                error(
                    "Titik potong melewati durasi audio: "
                    + ", ".join(ff.timecode(p) for p in over)
                )
                continue
        if any(p <= 0 for p in points):
            error("Titik potong harus lebih besar dari 00:00:00.")
            continue
        if not points:
            error("Tidak ada titik potong yang terbaca.")
            continue
        return points


def ask_chunk_length(duration: float | None) -> float:
    """Minta durasi tiap bagian, mis. `00:29:59`."""
    section("Durasi tiap bagian")
    note("Contoh: 00:29:59  ·  00:05:00  ·  30 (30 detik)")
    if duration:
        note(f"Durasi audio: {ff.format_seconds(duration)}")
    while True:
        raw = _input("  [bold]Setiap[/] [dim](contoh 00:29:59)[/] [bold]›[/] ")
        if not raw:
            error("Isi durasi tiap bagian.")
            continue
        try:
            value = parse_time(raw)
        except ValueError:
            error("Waktu tidak valid. Gunakan HH:MM:SS atau MM:SS.")
            continue
        if value is None or value <= 0:
            error("Durasi harus lebih besar dari nol.")
            continue
        if duration and value > duration:
            error(
                f"Durasi bagian ({ff.timecode(value)}) lebih panjang dari "
                f"seluruh audio ({ff.timecode(duration)})."
            )
            continue
        if duration and value < 1:
            error("Durasi bagian minimal 1 detik.")
            continue
        return value


def show_split_preview(ranges: list[tuple[float, float | None]], ext: str) -> None:
    if not ranges:
        warn("Tidak ada bagian yang bisa dibuat.")
        return
    table = Table(box=box.SIMPLE_HEAD, header_style="bold cyan", pad_edge=False)
    table.add_column("#", justify="right")
    table.add_column("Mulai", justify="right")
    table.add_column("Selesai", justify="right")
    table.add_column("Durasi", justify="right")
    table.add_column("Nama file")
    # tampilkan semua bagian bila masih wajar; kalau sangat banyak, ringkas
    limit = max(len(ranges), 0) if len(ranges) <= 20 else 8
    for i, (start, end) in enumerate(ranges[:limit], 1):
        length = (end - start) if end is not None else None
        table.add_row(
            str(i),
            ff.timecode(start),
            ff.timecode(end),
            ff.format_seconds(length) if length is not None else "?",
            Text(f"… bagian {i:02d}.{ext}"),
        )
    if len(ranges) > limit:
        remaining = len(ranges) - limit
        table.add_row("…", "", "", "", Text(f"dan {remaining} bagian lain"))
    console.print(
        Panel(
            table,
            title=f"[cyan]Rencana split[/] [dim]({len(ranges)} bagian)[/]",
            box=box.ROUNDED,
            border_style="dim",
        )
    )


def run_audio_split(
    path: Path,
    outdir: Path,
    ranges: list[tuple[float, float | None]],
    info: ff.MediaInfo | None = None,
) -> list[Path]:
    """Jalankan split audio tanpa re-encode, satu bagian per rentang."""
    if info is None:
        try:
            info = ff.probe(path)
        except ff.ToolError as exc:
            error(str(exc))
            return []
        show_media_info(info)
    if not info.has_audio:
        error("File ini tidak memiliki track audio untuk dipotong.")
        return []

    ext = ff.audio_stream_ext(info.audio)
    total = len(ranges)
    outputs: list[Path] = []

    with _Progress() as ui:
        ui.start_task("menyiapkan", total=None)

        def on_part(dst: Path, part_no: int, parts: int) -> None:
            ui.stage(f"memotong bagian {part_no}/{parts}…")
            ui.reset_task(None)

        def on_progress(done: float, seg_len: float | None) -> None:
            ui.update(done, seg_len)

        try:
            outputs = ff.split_audio_ranges(
                info, outdir, ranges,
                on_progress=on_progress, on_part=on_part,
            )
        except ff.ToolError as exc:
            ui.hide()
            error(str(exc))
            return []
        except KeyboardInterrupt:
            ui.hide()
            error("Dibatalkan. Bagian yang sudah selesai tetap tersimpan.")
            return []
        ui.hide()
    return outputs


def ask_urls() -> list[str]:
    section("URL sumber")
    note("Boleh beberapa URL sekaligus, pisahkan dengan spasi.")
    while True:
        raw = ask("URL")
        if not raw:
            continue
        parts = [p for p in re.split(r"[\s,]+", raw) if p]
        good = [p for p in parts if dl.URL_RE.match(p)]
        if good:
            return good
        error("Tidak ada URL http/https yang valid.")


# ===================================================================== info view

def flush() -> None:
    """Pastikan output sebelumnya tampil sebelum prompt berikutnya."""
    try:
        console.file.flush()
    except Exception:
        pass


def show_media_info(info: ff.MediaInfo) -> None:
    table = Table(box=box.SIMPLE, show_header=False, pad_edge=False)
    table.add_column(style="dim", width=10)
    table.add_column()
    table.add_row("File", Text(info.path.name, style="bold"))
    table.add_row("Durasi", info.duration_text)
    if info.video:
        table.add_row("Video", info.video.label)
    if info.audio:
        table.add_row("Audio", info.audio.label)
    if len(info.streams) > 2:
        others = ", ".join(s.label for s in info.streams[2:])
        table.add_row("Lainnya", Text(others))
    console.print(Panel(table, title="[cyan]Info media[/]", box=box.ROUNDED, border_style="dim"))


def show_url_info(info: dict) -> None:
    table = Table(box=box.SIMPLE, show_header=False, pad_edge=False)
    table.add_column(style="dim", width=10)
    table.add_column()
    table.add_row("Judul", Text(str(info.get("title") or "?"), style="bold"))
    dur = info.get("duration")
    if dur:
        table.add_row("Durasi", ff.format_seconds(dur))
    table.add_row("Sumber", Text(str(info.get("extractor_key") or info.get("extractor") or "?")))
    uploader = info.get("uploader")
    if uploader:
        table.add_row("Kanal", Text(str(uploader)))
    console.print(Panel(table, title="[cyan]Info video[/]", box=box.ROUNDED, border_style="dim"))


def show_formats(info: dict) -> None:
    rows = dl.format_table(info, limit=15)
    if not rows:
        warn("Tidak ada daftar format yang bisa ditampilkan.")
        return
    table = Table(box=box.SIMPLE_HEAD, header_style="bold cyan", pad_edge=False)
    for col in ("ID", "Jenis", "Resolusi", "Ext"):
        table.add_column(col)
    for row in rows:
        table.add_row(*(Text(cell) for cell in row))
    console.print(Panel(table, title="[cyan]Format tersedia (sebagian)[/]", box=box.ROUNDED, border_style="dim"))


# ===================================================================== progress

class StageColumn(ProgressColumn):
    """Menampilkan tahap pekerjaan saat ini."""

    def __init__(self) -> None:
        super().__init__()
        self.stage = ""

    def render(self, task) -> Text:
        return Text(self.stage or task.description, style="cyan")


class SizeColumn(ProgressColumn):
    """Ukuran terunduh; disembunyikan bila total belum diketahui."""

    def render(self, task) -> Text:
        if not task.total:
            return Text("")
        return DownloadColumn().render(task)


class PercentColumn(ProgressColumn):
    """Persentase; disembunyikan bila total belum diketahui."""

    def render(self, task) -> Text:
        if not task.total:
            return Text("")
        return Text(f"{task.percentage:>3.0f}%", style="bold")


class SpeedColumn(ProgressColumn):
    """Kecepatan unduh; kosong untuk pekerjaan non-unduh."""

    def render(self, task) -> Text:
        if not task.total:
            return Text("")
        return TransferSpeedColumn().render(task)


class EtaColumn(ProgressColumn):
    """Sisa waktu; kosong bila tidak relevan."""

    def render(self, task) -> Text:
        if not task.total:
            return Text("")
        return TimeRemainingColumn().render(task)


class _Progress:
    """Pembungkus rich Progress yang aman dipanggil dari hook yt-dlp."""

    def __init__(self, title: str = "") -> None:
        self.title = title
        self.stage_col = StageColumn()
        self.progress = Progress(
            SpinnerColumn(style="cyan"),
            self.stage_col,
            BarColumn(bar_width=None, complete_style="cyan", finished_style="green"),
            PercentColumn(),
            SizeColumn(),
            SpeedColumn(),
            EtaColumn(),
            console=console,
            transient=True,
            expand=True,
        )
        self.task: TaskID | None = None
        self.headline = Text(title, style="bold")

    def __enter__(self) -> "_Progress":
        self.progress.__enter__()
        if self.title:
            console.print(self.headline)
        return self

    def __exit__(self, *exc) -> None:
        self.progress.__exit__(*exc)

    # ---- control
    def stage(self, text: str) -> None:
        self.stage_col.stage = text

    def start_task(self, description: str = "memproses", total: float | None = None) -> None:
        self.task = self.progress.add_task(description, total=total)

    def reset_task(self, total: float | None, description: str | None = None) -> None:
        if self.task is None:
            self.start_task(description or "memproses", total)
        else:
            self.progress.reset(self.task, total=total, completed=0, visible=True)
            if description:
                self.progress.update(self.task, description=description)

    def update(self, done: float, total: float | None = None) -> None:
        if self.task is None:
            self.start_task(total=total)
        assert self.task is not None
        kwargs: dict = {"completed": done}
        if total:
            kwargs["total"] = total
        self.progress.update(self.task, **kwargs)

    def finish(self) -> None:
        if self.task is not None:
            total = self.progress.tasks[self.task].total
            if total:
                self.progress.update(self.task, completed=total)

    def hide(self) -> None:
        if self.task is not None:
            self.progress.update(self.task, visible=False)


def _human_bytes(n: float | None) -> str:
    if not n:
        return "?"
    units = ["B", "KB", "MB", "GB", "TB"]
    idx = 0
    value = float(n)
    while value >= 1000 and idx < len(units) - 1:
        value /= 1000
        idx += 1
    return f"{value:.1f} {units[idx]}"


# ==================================================================== job flows

def run_download_job(
    url: str,
    mode: str,
    segment: Segment,
    outdir: Path,
    audio_fmt: str = "mp3",
    audio_bitrate: str | None = "192k",
    bin_dir: str = "",
) -> list[Path]:
    rq = dl.DownloadRequest(
        url=url,
        mode=mode,
        outdir=outdir,
        start=segment.start,
        end=segment.end,
        audio_format=audio_fmt,
        audio_bitrate=audio_bitrate,
    )

    with _Progress() as ui:
        ui.stage("mengambil info…")
        ui.start_task("menghubungkan", total=None)

        def hook(ev: dict) -> None:
            kind = ev.get("event")
            if kind == "extract":
                ui.stage("mengambil info…")
            elif kind == "download":
                label = "mengunduh audio" if mode == "audio" else "mengunduh video"
                ui.stage(label)
                if ev.get("title") and not ui.headline.plain.strip():
                    pass
                total = ev.get("total")
                ui.update(ev.get("done") or 0, total)
            elif kind == "download_finished":
                ui.stage("unduhan selesai, menunggu pemrosesan…")
            elif kind == "postprocess":
                name = str(ev.get("name") or "")
                nice = {
                    "Merger": "menggabungkan audio + video (ffmpeg)…",
                    "MoveFiles": "merapikan file…",
                }.get(name, f"post-processing: {name}…")
                ui.stage(nice)
            elif kind == "convert_audio":
                ui.stage(f"mengonversi audio → {Path(str(ev.get('path'))).suffix.lstrip('.')}…")
                ui.reset_task(None)
            elif kind == "remux":
                ui.stage("menyusun ulang kontainer (ffmpeg)…")
                ui.reset_task(None)
            elif kind == "ffmpeg":
                ui.update(ev.get("done") or 0, ev.get("total"))

        try:
            outputs = dl.download(rq, bin_dir, progress=hook)
        except dl.UrlNotSupported as exc:
            ui.hide()
            error(str(exc))
            return []
        except dl.DownloadFailed as exc:
            ui.hide()
            error(str(exc))
            return []
        else:
            ui.finish()
            return outputs


def run_local_split(
    path: Path,
    outdir: Path,
    want_audio: bool,
    want_video: bool,
    audio_fmt: str,
    audio_bitrate: str | None,
    segment: Segment,
    info: ff.MediaInfo | None = None,
) -> list[Path]:
    if info is None:
        try:
            info = ff.probe(path)
        except ff.ToolError as exc:
            error(str(exc))
            return []
        show_media_info(info)
    results: list[Path] = []
    with _Progress() as ui:
        ui.start_task("memproses", total=None)

        def cb(done: float, total: float | None) -> None:
            ui.update(done, total)

        def run(label: str, fn, *args) -> None:
            ui.stage(label)
            ui.reset_task(None)
            try:
                out = fn(*args, start=segment.start, end=segment.end, on_progress=cb)
            except ff.ToolError as exc:
                error(f"{label}: {exc}")
                return
            ui.finish()
            results.append(out)
            success(out.name)

        if want_audio:
            if info.has_audio:
                run("mengekstrak audio…", ops.extract_audio, info, audio_fmt, outdir, audio_bitrate)
            else:
                warn("Tidak ada track audio untuk diekstrak.")
        if want_video:
            if info.has_video:
                run("mengekstrak video (tanpa suara)…", ops.extract_video, info, outdir, False)
            else:
                warn("Tidak ada track video untuk diekstrak.")
        ui.hide()
    return results


def run_local_clip(
    path: Path,
    outdir: Path,
    segment: Segment,
    keep_audio: bool,
    keep_video: bool,
    info: ff.MediaInfo | None = None,
) -> list[Path]:
    if info is None:
        try:
            info = ff.probe(path)
        except ff.ToolError as exc:
            error(str(exc))
            return []
        show_media_info(info)
    results: list[Path] = []
    with _Progress() as ui:
        ui.start_task("memotong", total=None)

        def cb(done: float, total: float | None) -> None:
            ui.update(done, total)

        try:
            if keep_audio and keep_video:
                ui.stage("memotong audio + video…")
                out = ops.extract_both(info, outdir, segment.start, segment.end, cb)
                results.append(out)
                success(out.name)
            else:
                if keep_audio:
                    ui.stage("memotong audio…")
                    out = ops.extract_audio(info, "m4a", outdir, "192k", segment.start, segment.end, cb)
                    results.append(out)
                    success(out.name)
                if keep_video:
                    ui.stage("memotong video…")
                    out = ops.extract_video(info, outdir, False, segment.start, segment.end, cb)
                    results.append(out)
                    success(out.name)
        except ff.ToolError as exc:
            error(str(exc))
        ui.hide()
    return results


# =================================================================== gabung teks

def ask_text_files(base: Path | None = None) -> list[Path] | None:
    """Kumpulkan daftar file teks dari input pengguna."""
    section("File teks yang digabung")
    note("Ketik satu path per baris; beberapa sekaligus dipisah koma.")
    note("Bisa juga pola: buku\\*.txt   ·   atau ketik FORDER untuk memilih semua file di folder.")
    note("Kosongkan input untuk selesai memasukkan.")
    files: list[Path] = []
    while True:
        raw = _input("  [bold]Tambah file[/] [bold]›[/] ")
        if not raw:
            if not files:
                error("Belum ada file yang dipilih.")
                continue
            return files
        if raw.strip().lower() in {"folder", "dir", "direktori"}:
            folder = ask_folder(base)
            if folder is None:
                continue
            picked = tx.list_text_files(folder)
            if not picked:
                warn(f"Tidak ada file di {folder}")
                continue
            files.extend(picked)
            success(f"{len(picked)} file ditambahkan dari {folder.name}")
            continue
        found = tx.expand_paths(raw, base)
        if not found:
            error(f"Tidak ditemukan: {raw}")
            continue
        added = 0
        for path in found:
            if path.is_dir():
                picked = tx.list_text_files(path)
                files.extend(picked)
                added += len(picked)
                continue
            if not path.exists():
                error(f"Tidak ditemukan: {path}")
                continue
            if path in files:
                warn(f"Sudah ada di daftar, dilewati: {path.name}")
                continue
            files.append(path)
            added += 1
        if added:
            success(f"{added} file ditambahkan")


def ask_folder(base: Path | None = None) -> Path | None:
    raw = ask("Path folder", str(base) if base else None)
    if not raw:
        return None
    p = _folder_from_input(raw.strip().strip('"').strip("'"))
    if not p.is_dir():
        error(f"Folder tidak ditemukan: {p}")
        return None
    return p


def show_file_order(files: list[Path]) -> None:
    table = Table(box=box.SIMPLE_HEAD, header_style="bold cyan", pad_edge=False)
    table.add_column("#", justify="right")
    table.add_column("File")
    table.add_column("Ukuran", justify="right", style="dim")
    for i, path in enumerate(files, 1):
        try:
            size = _human_bytes(path.stat().st_size)
        except OSError:
            size = "?"
        table.add_row(str(i), Text(path.name), size)
    console.print(
        Panel(
            table,
            title=f"[cyan]Urutan penggabungan[/] [dim]({len(files)} file)[/]",
            box=box.ROUNDED,
            border_style="dim",
        )
    )


def ask_order(files: list[Path]) -> list[Path]:
    """Tawarkan pengurutan ulang; urutan default mengikuti input."""
    show_file_order(files)
    if len(files) < 2:
        return files
    choice = menu(
        [
            ("1", "Pakai urutan seperti di atas"),
            ("2", "Urutkan otomatis berdasarkan nama file (bab2 sebelum bab10)"),
            ("3", "Urutkan otomatis berdasarkan tanggal diubah"),
            ("4", "Balik urutannya"),
            ("5", "Saya tentukan urutan sendiri"),
        ],
        title="Urutan file",
    )
    if choice == "0" or choice == "1":
        return files
    if choice == "2":
        return sorted(files, key=lambda p: tx.natural_key(p.name))
    if choice == "3":
        return sorted(files, key=_mtime)
    if choice == "4":
        return list(reversed(files))
    return _reorder_manually(files)


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _reorder_manually(files: list[Path]) -> list[Path]:
    remaining = list(files)
    ordered: list[Path] = []
    section("Tentukan urutan")
    note("Ketik nomor file sesuai urutan yang diinginkan, mis. 3 1 2")
    note("Kosongkan untuk memakai sisa file sesuai urutan sekarang.")
    while remaining:
        show_file_order(remaining)
        raw = _input("  [bold]Nomor[/] [bold]›[/] ")
        if not raw:
            ordered.extend(remaining)
            break
        picked: list[Path] = []
        bad = False
        for token in re.split(r"[\s,]+", raw):
            if not token:
                continue
            if not token.isdigit() or not 1 <= int(token) <= len(remaining):
                error(f"Nomor tidak valid: {token}")
                bad = True
                break
            path = remaining[int(token) - 1]
            if path not in picked:
                picked.append(path)
        if bad or not picked:
            continue
        ordered.extend(picked)
        remaining = [p for p in remaining if p not in picked]
    return ordered


def ask_merge_style() -> str:
    section("Gaya penggabungan")
    table = Table(box=box.SIMPLE, show_header=False, pad_edge=False)
    table.add_column(style="bold cyan", width=3)
    table.add_column()
    table.add_row("1", "Sambung langsung [dim](tanpa pemisah)[/]")
    table.add_row("2", "Satu baris kosong antar file [dim](disarankan)[/]")
    table.add_row("3", "Judul nama file + garis pemisah")
    console.print(table)
    while True:
        raw = _input("  [bold]›[/] [dim](2)[/] ") or "2"
        if raw in {"1", "2", "3"}:
            return {"1": "plain", "2": "blank", "3": "heading"}[raw]
        error("Pilih 1, 2, atau 3.")


def ask_output_encoding() -> str:
    section("Encoding file hasil")
    for i, (key, label) in enumerate(tx.OUTPUT_ENCODINGS.items(), 1):
        console.print(f"  [bold cyan]{i}[/]  {key:<12} [dim]{label}[/]")
    keys = list(tx.OUTPUT_ENCODINGS)
    while True:
        raw = _input("  [bold]›[/] [dim](1)[/] ") or "1"
        if raw.isdigit() and 1 <= int(raw) <= len(keys):
            return keys[int(raw) - 1]
        if raw in keys:
            return raw
        error("Pilih nomor yang tersedia.")


def ask_output_name(style_hint: str = "gabungan") -> str:
    raw = ask("Nama file hasil", f"{style_hint}.txt")
    name = raw.strip().strip('"').strip("'")
    if not name:
        name = f"{style_hint}.txt"
    if not Path(name).suffix:
        name += ".txt"
    return name


def show_merge_preview(result: tx.MergeResult) -> None:
    table = Table(box=box.SIMPLE, show_header=False, pad_edge=False)
    table.add_column(style="dim", width=16)
    table.add_column()
    table.add_row("File hasil", Text(result.output.name, style="bold"))
    empty = [s for s in result.sources if s.chars == 0]
    count = f"{len(result.sources)}"
    if empty:
        count += f" ({len(empty)} kosong)"
    table.add_row("Jumlah file", count)
    table.add_row("Total karakter", f"{result.total_chars:,}".replace(",", "."))
    table.add_row(
        "Total baris", f"{sum(s.lines for s in result.sources):,}".replace(",", ".")
    )
    if result.skipped:
        table.add_row("Dilewati", Text(f"{len(result.skipped)} file", style="yellow"))
    encodings = sorted({s.encoding for s in result.sources})
    table.add_row("Encoding sumber", ", ".join(encodings))
    console.print(
        Panel(table, title="[green]Hasil gabung[/]", box=box.ROUNDED, border_style="green")
    )
    for path, reason in result.skipped:
        warn(f"Dilewati: {path.name} — {reason}")
    console.print(f"  [dim]Tersimpan di:[/] {escape(str(result.output))}")


def run_text_merge(
    files: list[Path],
    output: Path,
    style: str,
    encoding: str,
) -> tx.MergeResult | None:
    with _Progress() as ui:
        ui.stage(f"menggabungkan {len(files)} file…")
        ui.start_task("membaca file", total=len(files))
        try:
            result = tx.merge_files(files, output, style=style, encoding=encoding)
        except tx.TextMergeError as exc:
            ui.hide()
            error(str(exc))
            return None
        ui.update(len(files), len(files))
        ui.hide()
    return result


def show_result(outputs: list[Path], outdir: Path) -> None:
    if not outputs:
        return
    console.print()
    table = Table(box=box.SIMPLE, show_header=False, pad_edge=False)
    table.add_column(style="green")
    table.add_column()
    table.add_column(style="dim", justify="right")
    for out in outputs:
        try:
            size = _human_bytes(out.stat().st_size)
        except OSError:
            size = "?"
        table.add_row("✔", Text(out.name), size)
    console.print(Panel(table, title="[green]Hasil[/]", box=box.ROUNDED, border_style="green", padding=(0, 1)))
    console.print(f"  [dim]Tersimpan di:[/] {escape(str(outdir))}")
