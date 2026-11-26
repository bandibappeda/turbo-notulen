"""Antarmuka terminal: kerangka tampilan, prompt, kartu, dan progress.

Prinsip:
- satu bahasa desain (warna, ikon, jarak) untuk semua layar;
- setiap alur bernomor langkah (1/4, 2/4, …) agar pengguna tahu posisinya;
- teks dinamis selalu di-escape sebelum dicetak (rich memakan `[...]`).
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from rich import box
from rich.align import Align
from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.progress import BarColumn, Progress, ProgressColumn, SpinnerColumn, TaskID
from rich.table import Table
from rich.text import Text

from . import downloader as dl
from . import ffmpeg_tools as ff
from . import operations as ops
from . import textmerge as tx

console = Console(highlight=False)

# --------------------------------------------------------------------- palet

ACCENT = "cyan"
MUTED = "grey53"
FAINT = "grey35"
OK = "green"
WARN = "yellow"
ERR = "red"
BORDER = "grey30"

AUDIO_CHOICES: list[tuple[str, str, str, str]] = [
    ("mp3", "192k", "MP3 192 kbps", "seimbang, paling umum"),
    ("mp3", "128k", "MP3 128 kbps", "kecil, cukup untuk podcast"),
    ("mp3", "320k", "MP3 320 kbps", "kualitas tertinggi"),
    ("m4a", "192k", "M4A (AAC) 192 kbps", "efisien, cocok untuk Apple"),
    ("opus", "160k", "Opus 160 kbps", "paling efisien untuk suara"),
    ("wav", "-", "WAV", "tanpa kompresi, besar"),
    ("flac", "-", "FLAC", "lossless terkompresi"),
]

# (gaya, encoding, label, keterangan)
MERGE_PRESETS: list[tuple[str, str, str, str]] = [
    ("blank", "utf-8", "Baris kosong antar file", "paling aman, utf-8"),
    ("heading", "utf-8", "Dengan judul tiap file", "jelas asal-usulnya"),
    ("plain", "utf-8", "Sambung langsung", "tanpa pemisah"),
    ("blank", "utf-8-sig", "Baris kosong + BOM", "untuk Notepad/Excel lama"),
]


# ==================================================================== kerangka

def flush() -> None:
    try:
        console.file.flush()
    except Exception:
        pass


def width() -> int:
    try:
        return console.size.width
    except Exception:
        return 80


def banner() -> None:
    """Kepala aplikasi: nama, ringkasan fungsi, dan status alat."""
    title = Text()
    title.append("splitmedia", style=f"bold {ACCENT}")
    title.append("  split audio/video", style=MUTED)
    console.print()
    console.print(Align.center(title))
    console.print(Align.center(Text(_status_line(), style=FAINT)))
    console.print()


def _status_line() -> str:
    try:
        import yt_dlp

        yt = yt_dlp.version.__version__
    except Exception:
        yt = "?"
    from . import __version__

    try:
        ff_ok = os.path.isfile(ff.ffmpeg_exe())
    except Exception:
        ff_ok = False
    ff_text = "ffmpeg siap" if ff_ok else "ffmpeg belum siap"
    return f"v{__version__}   ·   yt-dlp {yt}   ·   {ff_text}"


def step(index: int, total: int, title: str, subtitle: str | None = None) -> None:
    """Kepala satu langkah, mis. `2/4  Sumber`."""
    head = Text()
    head.append("  ")
    head.append(f"{index}/{total}", style=FAINT)
    head.append("  ")
    head.append(title, style="bold")
    console.print(head)
    if subtitle:
        console.print(Text("     " + subtitle, style=MUTED))


def note(text: str) -> None:
    """Catatan kecil yang ikut membungkus di lebar terminal."""
    console.print(Text("     " + str(text), style=MUTED), soft_wrap=True)


def success(text: str) -> None:
    console.print(Text.assemble(("  ✔ ", OK), (str(text), "")))


def warn(text: str) -> None:
    console.print(_message("!", WARN, text))


def error(text: str) -> None:
    console.print(_message("✘", ERR, text))


def info(text: str) -> None:
    console.print(_message("·", ACCENT, text))


def _message(mark: str, style: str, text: str) -> Text:
    lines = [ln.strip() for ln in str(text).splitlines() if ln.strip()]
    body = "\n     ".join(lines) if lines else str(text)
    return Text.assemble(("  " + mark + " ", style), (body, ""))


def rule(label: str = "") -> None:
    line = "─" * max(0, min(width() - 4, 64))
    text = Text("  " + line, style=FAINT)
    if label:
        text = Text.assemble(("  ", ""), (label + " ", MUTED), (line, FAINT))
    console.print(text)


def bye(text: str = "Sampai jumpa!") -> None:
    console.print()
    console.print(Text("  " + text, style=MUTED))
    console.print()


def print_block(renderable, indent: int = 2) -> None:
    """Cetak renderable dengan margin kiri yang seragam.

    `Table` membuang spasi awal dan `Padding` melebarkan isi ke lebar terminal,
    jadi blok dirender ke teks lebih dulu lalu tiap baris diberi margin.
    """
    with console.capture() as capture:
        console.print(renderable)
    prefix = " " * indent
    for line in capture.get().rstrip("\n").split("\n"):
        if line.strip():
            console.print(prefix + line)
        else:
            console.print()


class Steps:
    """Penomoran langkah yang jujur: total dihitung dari langkah yang benar-benar ada."""

    def __init__(self, titles: list[str]) -> None:
        self.titles = titles
        self.index = 0

    def next(self, subtitle: str | None = None) -> None:
        if self.index >= len(self.titles):
            return
        title = self.titles[self.index]
        self.index += 1
        step(self.index, len(self.titles), title, subtitle)


# ===================================================================== kartu

def card(title: str, rows: list[tuple[str, str | Text]], *, color: str = BORDER) -> None:
    """Kartu ringkas: label kiri (redup) + nilai kanan."""
    table = Table(box=None, show_header=False, show_edge=False, pad_edge=False, padding=(0, 1))
    table.add_column(style=MUTED, no_wrap=True)
    table.add_column(overflow="fold")
    for label, value in rows:
        if value == "":
            table.add_row("", "")
            continue
        table.add_row(label, value if isinstance(value, Text) else Text(str(value)))
    print_block(Panel(
        table,
        title=Text(" " + title + " ", style=ACCENT),
        title_align="left",
        box=box.ROUNDED,
        border_style=color,
        expand=False,
        padding=(0, 1),
    ))


# ==================================================================== prompt

def _input(prompt: str) -> str:
    try:
        flush()
        return console.input(prompt).strip()
    except (EOFError, KeyboardInterrupt):
        console.print()
        raise SystemExit(0) from None


def pause() -> None:
    console.print()
    _input(f"  [{MUTED}]Enter untuk kembali ke menu…[/]")


def _option_rows(rows: list[tuple], back: str | None = None) -> None:
    """Cetak tabel pilihan bernomor: (kunci, label[, keterangan])."""
    table = Table(
        box=None, show_header=False, show_edge=False, pad_edge=False,
        padding=(0, 2), expand=False,
    )
    table.add_column(style=f"bold {ACCENT}", no_wrap=True, justify="right")
    table.add_column(no_wrap=True)
    table.add_column(style=MUTED, overflow="fold")
    for option in rows:
        desc = option[2] if len(option) > 2 else ""
        table.add_row(option[0], option[1], desc)
    if back is not None:
        table.add_row("0", back, "")
    print_block(table)


def _read_choice(keys: set[str], default: str | None = None,
                 allow_back: bool = False) -> str | None:
    """Baca satu pilihan; ulangi sampai valid. `None` bila kembali."""
    suffix = f" [{MUTED}]({escape(default)})[/]" if default else ""
    while True:
        raw = _input(f"  [{ACCENT}]›[/]{suffix} ")
        if not raw and default is not None:
            return default
        if raw in keys:
            return raw
        if allow_back and raw == "0":
            return None
        error("Pilihan tidak tersedia.")


def menu(options: list[tuple], *, title: str = "Menu", back: str = "Kembali") -> str:
    """Menu bernomor; opsi boleh (kunci, label) atau (kunci, label, keterangan)."""
    console.print()
    console.print(Text("  " + title, style="bold"))
    _option_rows(options, back)
    keys = {opt[0] for opt in options}
    choice = _read_choice(keys | {"0"})
    return choice or "0"


def ask(prompt: str, default: str | None = None) -> str:
    suffix = f" [{MUTED}]({escape(str(default))})[/]" if default else ""
    raw = _input(f"  {escape(prompt)}{suffix} [{ACCENT}]›[/] ")
    if not raw and default is not None:
        return default
    return raw


def confirm(prompt: str, default: bool = False) -> bool:
    """Pertanyaan ya/tidak; `[y/N]` dibuat literal agar tidak dimakan markup."""
    hint = "Y/n" if default else "y/N"
    suffix = f" [{MUTED}]({hint})[/]"
    while True:
        raw = _input(f"  {escape(prompt)}{suffix} [{ACCENT}]›[/] ").lower()
        if not raw:
            return default
        if raw in ("y", "ya", "yes"):
            return True
        if raw in ("n", "no", "tidak"):
            return False
        error("Jawab y atau n.")


def _folder_from_input(raw: str) -> Path:
    p = Path(os.path.expandvars(os.path.expanduser(raw)))
    # di Windows, "D:folder" berarti relatif terhadap drive D,
    # padahal pengguna hampir selalu bermaksud "D:\\folder"
    if p.drive and not p.root and p.anchor == str(p.drive):
        p = Path(f"{p.drive}\\{str(p)[2:].lstrip('\\/')}")
    return p


def ask_path(prompt: str, default: str | None = None) -> Path | None:
    raw = ask(prompt, default)
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


def ask_folder(prompt: str, default: Path | None = None) -> Path | None:
    raw = ask(prompt, str(default) if default else None)
    if not raw:
        return None
    p = _folder_from_input(raw.strip().strip('"').strip("'"))
    if not p.is_dir():
        error(f"Folder tidak ditemukan: {p}")
        return None
    return p


# ===================================================================== waktu

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


def parse_range(raw: str) -> tuple[float | None, float | None]:
    """Baca rentang `mulai-selesai` tanpa mengacaukan `HH:MM:SS-HH:MM:SS`.

    Pemisah adalah tanda minus TERAKHIR setelah sekian `:` — jadi
    `08:15-09:00` benar terbaca 08:15 sampai 09:00, bukan 8:15 sampai :09:00.
    """
    text = raw.strip()
    for index in range(len(text) - 1, -1, -1):
        if text[index] != "-":
            continue
        left = text[:index]
        # titik dua di kiri harus lebih banyak dari di kanan agar ini pemisah
        if left.count(":") >= 1 and text[index:].count(":") > left.count(":"):
            continue
        try:
            start = parse_time(left) if left.strip() else None
            end = parse_time(text[index + 1:]) if text[index + 1:].strip() else None
        except ValueError:
            raise
        return start, end
    raise ValueError(raw)


@dataclass
class Segment:
    start: float | None = None
    end: float | None = None

    @property
    def is_full(self) -> bool:
        return self.start is None and self.end is None


def ask_segment(duration: float | None = None) -> Segment:
    """Satu pertanyaan: rentang potongan (kosong = seluruh durasi)."""
    hint = "kosong = seluruh durasi · contoh 30-90 atau 00:30-01:30"
    if duration:
        hint = f"durasi {ff.format_seconds(duration)} · " + hint
    note(hint)
    while True:
        raw = _input(f"  Rentang [{MUTED}](semua)[/] [{ACCENT}]›[/] ")
        if not raw:
            return Segment()
        try:
            start, end = parse_range(raw)
        except ValueError:
            error("Format rentang: mulai-selesai, mis. 30-90 atau 00:30-01:30.")
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
    hint = "contoh 00:29:59 · 00:10:00, 01:02:03 · MM:SS juga bisa"
    if duration:
        hint = f"durasi {ff.format_seconds(duration)} · " + hint
    note(hint)
    while True:
        raw = _input(f"  Titik potong [{ACCENT}]›[/] ")
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
            if value is not None:
                points.append(value)
        if bad:
            error(f"Waktu tidak valid: {', '.join(bad)}. Gunakan MM:SS atau HH:MM:SS.")
            continue
        points = sorted(set(points))
        if not points:
            error("Tidak ada titik potong yang terbaca.")
            continue
        if any(p <= 0 for p in points):
            error("Titik potong harus lebih besar dari 00:00:00.")
            continue
        if duration:
            over = [p for p in points if p >= duration]
            if over:
                error("Melewati durasi: " + ", ".join(ff.timecode(p) for p in over))
                continue
        return points


def ask_chunk_length(duration: float | None) -> float:
    hint = "contoh 00:29:59 · 00:05:00 · 30 (detik)"
    if duration:
        hint = f"durasi {ff.format_seconds(duration)} · " + hint
    note(hint)
    while True:
        raw = _input(f"  Setiap [{MUTED}](00:29:59)[/] [{ACCENT}]›[/] ")
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
                f"Durasi bagian ({ff.timecode(value)}) melebihi seluruh audio "
                f"({ff.timecode(duration)})."
            )
            continue
        return value


# ==================================================================== pilihan

def _choice_table(rows: list[tuple[str, str, str]], default: str) -> str:
    _option_rows(rows)
    chosen = _read_choice({row[0] for row in rows}, default)
    return chosen or default


def ask_audio_profile() -> tuple[str, str | None]:
    rows = [(str(i), label, desc) for i, (_, _, label, desc) in enumerate(AUDIO_CHOICES, 1)]
    key = _choice_table(rows, "1")
    fmt, bitrate, *_ = AUDIO_CHOICES[int(key) - 1]
    return fmt, (None if bitrate == "-" else bitrate)


def ask_merge_preset() -> tuple[str, str]:
    """Kembalikan (gaya, encoding) dalam satu pertanyaan."""
    rows = [(str(i), label, desc) for i, (_, _, label, desc) in enumerate(MERGE_PRESETS, 1)]
    key = _choice_table(rows, "1")
    style, encoding, _label, _desc = MERGE_PRESETS[int(key) - 1]
    return style, encoding


def ask_urls() -> list[str]:
    note("beberapa URL sekaligus: pisahkan dengan spasi")
    while True:
        raw = _input(f"  URL [{ACCENT}]›[/] ")
        if not raw:
            continue
        parts = [p for p in re.split(r"[\s,]+", raw) if p]
        good = [p for p in parts if dl.URL_RE.match(p)]
        if good:
            return good
        error("Tidak ada URL http/https yang valid.")


def ask_output_path(default: Path) -> Path:
    """Satu pertanyaan: folder + nama berkas hasil (boleh ketik nama saja)."""
    raw = ask("Simpan hasil ke", str(default))
    raw = raw.strip().strip('"').strip("'")
    if not raw:
        return default
    path = _folder_from_input(raw)
    if not path.is_absolute() and not Path(raw).parent.parts:
        path = default.parent / raw  # hanya nama berkas
    if path.is_dir():
        path = path / default.name
    if not path.suffix:
        path = path.with_suffix(".txt")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        error(f"Tidak bisa membuat folder: {exc}")
        return default
    return path


# ====================================================================== info

def show_media_info(info: ff.MediaInfo) -> None:
    rows: list[tuple[str, str | Text]] = [
        ("File", Text(info.path.name, style="bold")),
        ("Durasi", info.duration_text),
    ]
    if info.video:
        rows.append(("Video", info.video.label))
    if info.audio:
        rows.append(("Audio", info.audio.label))
    if len(info.streams) > 2:
        rows.append(("Lainnya", ", ".join(s.label for s in info.streams[2:])))
    card("Info media", rows)


def show_url_info(info: dict) -> None:
    rows: list[tuple[str, str | Text]] = [
        ("Judul", Text(str(info.get("title") or "?"), style="bold")),
    ]
    if info.get("duration"):
        rows.append(("Durasi", ff.format_seconds(info["duration"])))
    rows.append(("Sumber", str(info.get("extractor_key") or info.get("extractor") or "?")))
    if info.get("uploader"):
        rows.append(("Kanal", str(info["uploader"])))
    card("Info media", rows)


def show_split_preview(ranges: list[tuple[float, float | None]], ext: str) -> None:
    if not ranges:
        warn("Tidak ada bagian yang bisa dibuat.")
        return
    table = Table(box=None, show_header=True, show_edge=False, pad_edge=False, padding=(0, 2))
    table.add_column("#", justify="right", style=MUTED)
    for name, justify in (("Mulai", "right"), ("Selesai", "right"), ("Durasi", "right")):
        table.add_column(name, justify=justify)
    limit = len(ranges) if len(ranges) <= 12 else 6
    for i, (start, end) in enumerate(ranges[:limit], 1):
        length = (end - start) if end is not None else None
        table.add_row(
            str(i),
            ff.timecode(start),
            ff.timecode(end),
            ff.format_seconds(length) if length is not None else "—",
        )
    if len(ranges) > limit:
        table.add_row("…", "", "", f"+{len(ranges) - limit} bagian")
    print_block(
        Panel(
            table,
            title=Text(f" Rencana · {len(ranges)} bagian ", style=ACCENT),
            title_align="left",
            subtitle=Text(f" hasil: .{ext} ", style=MUTED),
            subtitle_align="right",
            box=box.ROUNDED,
            border_style=BORDER,
            expand=False,
            padding=(0, 1),
        )
    )


def show_file_order(files: list[Path], title: str = "Urutan file") -> None:
    table = Table(box=None, show_header=False, show_edge=False, pad_edge=False, padding=(0, 2))
    table.add_column("#", justify="right", style=MUTED)
    table.add_column(overflow="ellipsis", no_wrap=True)
    table.add_column("Ukuran", justify="right", style=MUTED)
    limit = len(files) if len(files) <= 10 else 6
    for i, path in enumerate(files[:limit], 1):
        table.add_row(str(i), Text(path.name), human_size(_size_of(path)))
    if len(files) > limit:
        table.add_row("…", Text(f"+{len(files) - limit} file lain", style=MUTED), "")
    print_block(
        Panel(
            table,
            title=Text(f" {title} · {len(files)} file ".replace("  ·", "") if title else f" {len(files)} file ", style=ACCENT),
            title_align="left",
            box=box.ROUNDED,
            border_style=BORDER,
            expand=False,
            padding=(0, 1),
        )
    )


def show_merge_preview(result: tx.MergeResult) -> None:
    empty = [s for s in result.sources if s.chars == 0]
    count = str(len(result.sources)) + (f" ({len(empty)} kosong)" if empty else "")
    rows: list[tuple[str, str | Text]] = [
        ("File hasil", Text(result.output.name, style="bold")),
        ("Jumlah file", count),
        ("Total", f"{result.total_chars:,}".replace(",", ".") + " karakter"),
    ]
    if result.skipped:
        rows.append(("Dilewati", Text(f"{len(result.skipped)} file", style=WARN)))
    card("Hasil gabung", rows, color=OK)
    for path, reason in result.skipped:
        warn(f"Dilewati: {path.name} — {reason}")
    _saved_to(result.output.parent)


def show_result(outputs: list[Path], outdir: Path, title: str = "Selesai") -> None:
    if not outputs:
        return
    table = Table(box=None, show_header=False, show_edge=False, pad_edge=False, padding=(0, 2))
    table.add_column(style=OK, no_wrap=True)
    table.add_column(overflow="ellipsis", no_wrap=True)
    table.add_column(justify="right", style=MUTED)
    for out in outputs:
        table.add_row("✔", Text(out.name), human_size(_size_of(out)))
    console.print()
    print_block(
        Panel(
            table,
            title=Text(f" {title} ", style=OK),
            title_align="left",
            subtitle=Text(f" {len(outputs)} file ", style=MUTED),
            subtitle_align="right",
            box=box.ROUNDED,
            border_style=OK,
            expand=False,
            padding=(0, 1),
        )
    )
    _saved_to(outdir)


def _saved_to(outdir: Path) -> None:
    console.print(Text.assemble(("  Tersimpan di  ", MUTED), (str(outdir), "")))
    flush()


def show_about(rows: list[tuple[str, str | Text]]) -> None:
    card("Tentang", rows)


def _size_of(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def human_size(n: float | None) -> str:
    if not n:
        return "?"
    value = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1000 or unit == "TB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1000
    return f"{value:.1f} TB"


# ================================================================= progress

class StageColumn(ProgressColumn):
    """Teks tahap berjalan (menggantikan description bila diisi)."""

    def __init__(self) -> None:
        super().__init__()
        self.stage = ""

    def render(self, task) -> Text:
        return Text(self.stage or task.description, style=f"bold {ACCENT}")


class DetailColumn(ProgressColumn):
    """Persentase · ukuran · kecepatan · sisa waktu (hanya bila relevan)."""

    def render(self, task) -> Text:
        parts: list[tuple[str, str]] = []
        if task.total:
            parts.append((f"{task.percentage:3.0f}%", "bold"))
            parts.append(
                (f"{human_size(task.completed)} / {human_size(task.total)}", MUTED)
            )
            speed = task.finished_speed or task.speed
            if speed:
                parts.append((f"{human_size(speed)}/s", MUTED))
                remaining = max(0.0, (task.total - task.completed) / speed)
                parts.append((f"sisa {human_time(remaining)}", FAINT))
        elif task.completed:
            parts.append((human_size(task.completed), MUTED))
        return Text.assemble(*parts)


def human_time(seconds: float | None) -> str:
    if seconds is None or seconds != seconds or seconds < 0:
        return "?"
    seconds = int(round(seconds))
    if seconds < 60:
        return f"{seconds} dtk"
    return f"{seconds // 60} mnt {seconds % 60:02d}"


class _Progress:
    """Pembungkus rich Progress dengan API yang dipakai hook yt-dlp/ffmpeg."""

    def __init__(self, title: str = "") -> None:
        self.title = title
        self.stage_col = StageColumn()
        self.progress = Progress(
            SpinnerColumn("dots", style=ACCENT),
            self.stage_col,
            BarColumn(
                bar_width=max(8, min(26, width() - 74)),
                complete_style=ACCENT,
                finished_style=OK,
                pulse_style=FAINT,
            ),
            DetailColumn(),
            console=console,
            transient=True,
            expand=False,
        )
        self.task: TaskID | None = None

    def __enter__(self) -> "_Progress":
        self.progress.__enter__()
        if self.title:
            console.print(Text("  " + self.title, style="bold"))
        return self

    def __exit__(self, *exc) -> None:
        self.progress.__exit__(*exc)

    def stage(self, text: str) -> None:
        self.stage_col.stage = text

    def start_task(self, description: str = "memproses", total: float | None = None) -> None:
        self.task = self.progress.add_task(description, total=total)

    def reset_task(self, total: float | None, description: str | None = None) -> None:
        if self.task is None:
            self.start_task(description or "memproses", total)
            return
        self.progress.reset(self.task, total=total, completed=0)
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
        if self.task is None:
            return
        total = self.progress.tasks[self.task].total
        if total:
            self.progress.update(self.task, completed=total)

    def hide(self) -> None:
        if self.task is not None:
            self.progress.update(self.task, visible=False)


# =================================================================== pekerjaan

def run_download_job(
    url: str,
    mode: str,
    segment: Segment,
    outdir: Path,
    audio_fmt: str = "mp3",
    audio_bitrate: str | None = "192k",
    bin_dir: str = "",
) -> list[Path]:
    request = dl.DownloadRequest(
        url=url,
        mode=mode,
        outdir=outdir,
        start=segment.start,
        end=segment.end,
        audio_format=audio_fmt,
        audio_bitrate=audio_bitrate,
    )

    with _Progress() as ui:
        ui.stage("Mengambil info…")
        ui.start_task("menghubungkan", total=None)

        def hook(ev: dict) -> None:
            kind = ev.get("event")
            if kind == "extract":
                ui.stage("Mengambil info…")
            elif kind == "download":
                ui.stage("Mengunduh audio…" if mode == "audio" else "Mengunduh video…")
                ui.update(ev.get("done") or 0, ev.get("total"))
            elif kind == "download_finished":
                ui.stage("Merapikan hasil…")
            elif kind == "postprocess":
                name = str(ev.get("name") or "")
                ui.stage(
                    {
                        "Merger": "Menggabungkan audio + video…",
                        "MoveFiles": "Merapikan file…",
                    }.get(name, f"Memproses ({name})…")
                )
            elif kind == "convert_audio":
                ext = Path(str(ev.get("path"))).suffix.lstrip(".").upper()
                ui.stage(f"Mengonversi audio ke {ext}…")
                ui.reset_task(None)
            elif kind == "remux":
                ui.stage("Menyusun ulang kontainer…")
                ui.reset_task(None)
            elif kind == "ffmpeg":
                ui.update(ev.get("done") or 0, ev.get("total"))

        try:
            outputs = dl.download(request, bin_dir, progress=hook)
        except (dl.UrlNotSupported, dl.DownloadFailed) as exc:
            ui.hide()
            error(str(exc))
            return []
        except KeyboardInterrupt:
            ui.hide()
            error("Dibatalkan.")
            return []
        ui.finish()
        return outputs


def run_local_split(
    info: ff.MediaInfo,
    outdir: Path,
    want_audio: bool,
    want_video: bool,
    audio_fmt: str,
    audio_bitrate: str | None,
) -> list[Path]:
    results: list[Path] = []
    with _Progress() as ui:
        ui.start_task("memproses", total=None)

        def cb(done: float, total: float | None) -> None:
            ui.update(done, total)

        def run(label: str, fn) -> None:
            ui.stage(label)
            ui.reset_task(None)
            try:
                out = fn(on_progress=cb)
            except ff.ToolError as exc:
                error(f"{label.rstrip('…')} gagal: {exc}")
                return
            ui.finish()
            results.append(out)

        if want_audio:
            if info.has_audio:
                run("Mengekstrak audio…", lambda **kw: ops.extract_audio(
                    info, audio_fmt, outdir, audio_bitrate, **kw))
            else:
                warn("File ini tidak punya track audio.")
        if want_video:
            if info.has_video:
                run("Mengekstrak video…", lambda **kw: ops.extract_video(info, outdir, False, **kw))
            else:
                warn("File ini tidak punya track video.")
        ui.hide()
    return results


def run_local_clip(
    info: ff.MediaInfo,
    outdir: Path,
    segment: Segment,
    keep_audio: bool,
    keep_video: bool,
) -> list[Path]:
    results: list[Path] = []
    with _Progress() as ui:
        ui.start_task("memotong", total=None)

        def cb(done: float, total: float | None) -> None:
            ui.update(done, total)

        try:
            if keep_audio and keep_video:
                ui.stage("Memotong audio + video…")
                results.append(ops.extract_both(info, outdir, segment.start, segment.end, cb))
            else:
                if keep_audio:
                    ui.stage("Memotong audio…")
                    results.append(ops.extract_audio(
                        info, "m4a", outdir, "192k", segment.start, segment.end, cb))
                if keep_video:
                    ui.stage("Memotong video…")
                    results.append(ops.extract_video(
                        info, outdir, False, segment.start, segment.end, cb))
        except ff.ToolError as exc:
            error(str(exc))
        ui.hide()
    return results


def run_audio_split(
    info: ff.MediaInfo,
    outdir: Path,
    ranges: list[tuple[float, float | None]],
) -> list[Path]:
    if not info.has_audio:
        error("File ini tidak punya track audio.")
        return []
    with _Progress() as ui:
        ui.start_task("menyiapkan", total=None)

        def on_part(_dst: Path, part_no: int, parts: int) -> None:
            ui.stage(f"Memotong bagian {part_no}/{parts}…")
            ui.reset_task(None)

        try:
            outputs = ff.split_audio_ranges(
                info, outdir, ranges, on_progress=lambda d, t: ui.update(d, t), on_part=on_part
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


def run_text_merge(
    files: list[Path],
    output: Path,
    style: str,
    encoding: str,
) -> tx.MergeResult | None:
    with _Progress() as ui:
        ui.stage(f"Menggabungkan {len(files)} file…")
        ui.start_task("membaca", total=len(files))
        try:
            result = tx.merge_files(files, output, style=style, encoding=encoding)
        except tx.TextMergeError as exc:
            ui.hide()
            error(str(exc))
            return None
        ui.update(len(files), len(files))
        ui.hide()
    return result


# ================================================================ gabung teks

def ask_text_files(base: Path | None = None) -> list[Path] | None:
    note("satu path per baris · beberapa sekaligus dipisah koma")
    note("pola seperti *.txt juga bisa · kosongkan jika sudah selesai")
    files: list[Path] = []
    while True:
        raw = _input(f"  Tambah file [{ACCENT}]›[/] ")
        if not raw:
            if not files:
                error("Belum ada file yang dipilih.")
                continue
            return files
        found = tx.expand_paths(raw, base)
        if not found:
            error(f"Tidak ditemukan: {raw}")
            continue
        added = 0
        for path in found:
            if path.is_dir():
                picked = tx.list_text_files(path)
                if not picked:
                    warn(f"Tidak ada file di {path}")
                    continue
                files.extend(picked)
                added += len(picked)
                continue
            if not path.exists():
                error(f"Tidak ditemukan: {path}")
                continue
            if path in files:
                warn(f"Sudah ada: {path.name}")
                continue
            files.append(path)
            added += 1
        if added:
            success(f"{added} file ditambahkan")


def ask_order(files: list[Path]) -> list[Path]:
    # satu file: tidak ada yang perlu diurutkan, jangan tanya apa pun
    if len(files) < 2:
        show_file_order(files)
        return files
    console.print()
    console.print(Text("  Urutan file", style="bold"))
    show_file_order(files, title="")
    choice = menu(
        [
            ("1", "Urutan ini", "sesuai urutan penambahan"),
            ("2", "Urutkan menurut nama", "bab 2 sebelum bab 10"),
            ("3", "Urutkan menurut tanggal", "paling lama lebih dulu"),
            ("4", "Balik urutan", ""),
            ("5", "Atur sendiri", "ketik nomor file"),
        ],
        title="Pilih urutan",
        back="Urutan ini",
    )
    if choice in ("0", "1"):
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
    note("ketik nomor sesuai urutan, mis. 3 1 2 · kosong = sisanya apa adanya")
    while remaining:
        show_file_order(remaining, title="Sisa file")
        raw = _input(f"  Nomor [{ACCENT}]›[/] ")
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
