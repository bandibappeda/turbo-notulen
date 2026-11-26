"""Alur menu utama aplikasi."""
from __future__ import annotations

import sys
from pathlib import Path

from . import downloader
from . import ffmpeg_tools as ff
from . import ui
from .ui import (
    Segment,
    Steps,
    ask_audio_profile,
    ask_chunk_length,
    ask_cut_points,
    ask_merge_preset,
    ask_order,
    ask_outdir,
    ask_output_path,
    ask_path,
    ask_segment,
    ask_text_files,
    ask_urls,
    confirm,
    console,
    error,
    menu,
    warn,
)

SAMPLE_FORMATS = ", ".join(("MP3", "M4A", "Opus", "WAV", "FLAC"))


def _done(outputs: list[Path], outdir: Path, title: str = "Selesai") -> None:
    ui.show_result(outputs, outdir, title=title)
    if outputs:
        ui.pause()


def _no_output() -> None:
    console.print()
    warn("Tidak ada file yang berhasil dibuat.")
    ui.pause()


# ------------------------------------------------------------------- unduh URL

def _download_flow(bin_dir: str) -> None:
    while True:
        choice = menu(
            [
                ("1", "Audio saja", "unduh & konversi ke " + SAMPLE_FORMATS),
                ("2", "Video saja", "tanpa suara, MP4"),
                ("3", "Audio + video terpisah", "satu unduhan, dua file"),
            ],
            title="Unduh dari URL",
        )
        if choice == "0":
            return

        needs_audio = choice in ("1", "3")
        flow = Steps(["Sumber", "Format audio"] if needs_audio else ["Sumber"])
        flow.next()
        urls = ask_urls()
        audio_fmt, audio_bitrate = "mp3", "192k"
        if needs_audio:
            flow.next()
            audio_fmt, audio_bitrate = ask_audio_profile()

        mode = {"1": "audio", "2": "video", "3": "separate"}[choice]
        outdir: Path | None = None
        all_outputs: list[Path] = []
        for index, url in enumerate(urls, 1):
            label = f"Sumber {index}/{len(urls)}" if len(urls) > 1 else "Sumber"
            flow.next(label)
            try:
                with console.status("  Mengambil info…", spinner="dots"):
                    info = downloader.probe_url(url, bin_dir)
            except (downloader.UrlNotSupported, downloader.DownloadFailed) as exc:
                error(str(exc))
                continue
            except Exception as exc:  # noqa: BLE001 - jangan biarkan app crash
                error(f"{type(exc).__name__}: {exc}")
                continue
            ui.show_url_info(info)

            segment = Segment()
            if confirm("Ambil potongan waktu tertentu?"):
                segment = ask_segment(info.get("duration"))
                console.print()
            if outdir is None:
                outdir = ask_outdir()

            console.print()
            outputs = ui.run_download_job(
                url, mode, segment, outdir, audio_fmt, audio_bitrate, bin_dir
            )
            flow.index -= 1  # sumber berikutnya memakai nomor langkah yang sama
            all_outputs.extend(outputs)
        if outdir is None:
            _no_output()
        else:
            _done(all_outputs, outdir)


# ------------------------------------------------------------------ file lokal

def _local_flow() -> None:
    while True:
        choice = menu(
            [
                ("1", "Audio saja", "ekstrak track audio"),
                ("2", "Video saja", "tanpa suara"),
                ("3", "Keduanya", "dua file terpisah"),
            ],
            title="File lokal",
        )
        if choice == "0":
            return

        meta = _load_media()
        if meta is None:
            continue

        outdir = ask_outdir(meta.path.parent)
        audio_fmt, audio_bitrate = "mp3", "192k"
        if choice in ("1", "3"):
            console.print()
            audio_fmt, audio_bitrate = ask_audio_profile()

        console.print()
        outputs = ui.run_local_split(
            meta, outdir,
            want_audio=choice in ("1", "3"),
            want_video=choice in ("2", "3"),
            audio_fmt=audio_fmt,
            audio_bitrate=audio_bitrate,
        )
        _done(outputs, outdir)


def _load_media() -> ff.MediaInfo | None:
    """Minta path, validasi, lalu tampilkan info. None bila gagal/batal."""
    path = ask_path("Path file (bisa drag & drop)")
    if path is None:
        return None
    if path.is_dir():
        error("Itu sebuah folder, bukan file.")
        return None
    try:
        meta = ff.probe(path)
    except ff.ToolError as exc:
        error(str(exc))
        return None
    console.print()
    ui.show_media_info(meta)
    return meta


# ----------------------------------------------------------------- split audio

def _split_audio_flow() -> None:
    meta = _load_media()
    if meta is None:
        return
    if not meta.has_audio:
        error("File ini tidak punya track audio untuk dipotong.")
        ui.pause()
        return
    if meta.duration is None:
        warn("Durasi tidak terbaca; sebagian perhitungan mungkin tidak tersedia.")

    mode = menu(
        [
            ("1", "Pada titik tertentu", "mis. 00:10:00, 00:29:59"),
            ("2", "Setiap durasi tetap", "mis. tiap 00:29:59"),
        ],
        title="Cara memotong",
    )
    if mode == "0":
        return

    console.print()
    if mode == "1":
        points = ask_cut_points(meta.duration)
        ranges = ff.make_ranges_from_points(0.0, meta.duration or points[-1], points)
        if meta.duration is None:
            ranges[-1] = (ranges[-1][0], None)
    else:
        if meta.duration is None:
            error("Durasi tidak diketahui, potong berkala tidak bisa dihitung.")
            return
        chunk = ask_chunk_length(meta.duration)
        ranges = ff.make_ranges(0.0, meta.duration, chunk)

    console.print()
    ui.show_split_preview(ranges, ff.audio_stream_ext(meta.audio))
    outdir = ask_outdir(meta.path.parent)
    _done(ui.run_audio_split(meta, outdir, ranges), outdir, title="Selesai memotong")


# ------------------------------------------------------------------ potong 1x

def _local_clip_flow() -> None:
    meta = _load_media()
    if meta is None:
        return

    console.print()
    segment = ask_segment(meta.duration)
    if segment.is_full:
        warn("Rentang kosong — seluruh durasi akan diproses.")

    kind = menu(
        [("1", "Video + audio", "satu file gabungan"),
         ("2", "Audio saja", ""),
         ("3", "Video saja", "")],
        title="Isi potongan",
    )
    if kind == "0":
        return

    outdir = ask_outdir(meta.path.parent)
    console.print()
    outputs = ui.run_local_clip(
        meta, outdir, segment, kind in ("1", "2"), kind in ("1", "3")
    )
    _done(outputs, outdir, title="Selesai memotong")


# ----------------------------------------------------------------- gabung teks

def _merge_text_flow() -> None:
    files = ask_text_files()
    if not files:
        return

    ordered = ask_order(files)

    style, encoding = ask_merge_preset()

    base = ordered[0].parent
    default_name = "gabungan.txt" if len(ordered) == 1 else f"gabungan {len(ordered)} file.txt"
    output = ff.safe_output(ask_output_path(base / default_name))

    console.print()
    result = ui.run_text_merge(ordered, output, style, encoding)
    if result is None:
        return
    console.print()
    ui.show_merge_preview(result)
    ui.pause()


# ---------------------------------------------------------------------- tentang

def _about(bin_dir: str) -> None:
    import platform

    try:
        import yt_dlp

        yt_version = yt_dlp.version.__version__
    except Exception:
        yt_version = "?"
    from . import __version__

    ff_path = ff.ffmpeg_exe()
    ui.show_about(
        [
            ("Versi", f"splitmedia {__version__}"),
            ("Python", f"{platform.python_version()} ({platform.architecture()[0]})"),
            ("Sistem", f"{platform.system()} {platform.release()}"),
            ("yt-dlp", yt_version),
            ("FFmpeg", Path(ff_path).name),
            ("Lokasi", str(Path(ff_path).parent)),
            ("", ""),
            ("Sumber", "yt-dlp (github.com/yt-dlp/yt-dlp) + FFmpeg"),
        ]
    )
    ui.pause()


# ------------------------------------------------------------------------- main

def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if any(a in {"-h", "--help"} for a in argv):
        console.print(
            "splitmedia — menu interaktif split audio/video.\n\n"
            "Pakai:  python -m splitmedia\n"
            "        splitmedia            (bila diinstal)\n\n"
            "Semua pilihan diatur lewat menu setelah aplikasi dijalankan."
        )
        return 0

    try:
        bin_dir = ff.ensure_ffmpeg()
    except ff.ToolError as exc:
        error(str(exc))
        return 2

    ui.banner()

    while True:
        choice = menu(
            [
                ("1", "Unduh dari URL", "YouTube & situs lain"),
                ("2", "File lokal", "ekstrak audio / video"),
                ("3", "Split audio", "potong jadi banyak bagian"),
                ("4", "Potong satu segmen", "ambil rentang tertentu"),
                ("5", "Gabung teks", ".txt / .md jadi satu file"),
                ("6", "Tentang", "versi & komponen"),
            ],
            title="Menu",
            back="Keluar",
        )
        if choice == "0":
            console.print()
            ui.bye()
            return 0
        try:
            if choice == "1":
                _download_flow(bin_dir)
            elif choice == "2":
                _local_flow()
            elif choice == "3":
                _split_audio_flow()
            elif choice == "4":
                _local_clip_flow()
            elif choice == "5":
                _merge_text_flow()
            elif choice == "6":
                _about(bin_dir)
        except KeyboardInterrupt:
            console.print()
            warn("Dibatalkan.")
            continue
