"""Alur menu utama aplikasi."""
from __future__ import annotations

import sys
from pathlib import Path

from rich import box
from rich.markup import escape
from rich.panel import Panel

from . import downloader
from . import ffmpeg_tools as ff
from . import ui
from .ui import (
    Segment, ask, ask_audio_profile, ask_chunk_length, ask_cut_points,
    ask_outdir, ask_path, ask_segment, ask_urls, console, error, menu,
    show_result, show_split_preview, warn,
)


def _pause() -> None:
    console.print()
    ui.pause()


def _download_flow(bin_dir: str) -> None:
    while True:
        choice = menu(
            [
                ("1", "Audio saja — unduh & konversi ke MP3/M4A/Opus/WAV/FLAC"),
                ("2", "Video saja — unduh tanpa suara (MP4)"),
                ("3", "Audio + video terpisah — unduh sekali, hasilkan dua file"),
            ],
            title="Unduh dari URL",
        )
        if choice == "0":
            return
        urls = ask_urls()
        if not urls:
            continue

        audio_fmt, audio_bitrate = ("mp3", "192k")
        if choice in ("1", "3"):
            audio_fmt, audio_bitrate = ask_audio_profile()

        console.print()
        mode = {"1": "audio", "2": "video", "3": "separate"}[choice]
        outdir: Path | None = None
        all_outputs: list[Path] = []
        for idx, url in enumerate(urls, 1):
            if len(urls) > 1:
                console.print(f"  [bold cyan]({idx}/{len(urls)})[/] {escape(url)}")
            try:
                with console.status("[cyan]mengambil info…[/]", spinner="dots"):
                    info = downloader.probe_url(url, bin_dir)
            except downloader.UrlNotSupported as exc:
                error(str(exc))
                continue
            except downloader.DownloadFailed as exc:
                error(str(exc))
                continue
            except Exception as exc:  # noqa: BLE001 - jangan biarkan app crash
                error(f"{type(exc).__name__}: {exc}")
                continue
            ui.show_url_info(info)

            segment = Segment()
            clip = ask("Ambil potongan waktu tertentu? [y/N]", "n").lower().startswith("y")
            if clip:
                segment = ask_segment(info.get("duration"))
            if outdir is None:
                outdir = ask_outdir()
            console.print()
            outputs = ui.run_download_job(
                url, mode, segment, outdir, audio_fmt, audio_bitrate, bin_dir,
            )
            all_outputs.extend(outputs)
        if outdir is not None:
            show_result(all_outputs, outdir)
        if all_outputs:
            _pause()


def _local_flow() -> None:
    while True:
        choice = menu(
            [
                ("1", "Ekstrak audio dari file media"),
                ("2", "Ekstrak video (tanpa suara) dari file media"),
                ("3", "Pisahkan keduanya sekaligus"),
            ],
            title="File lokal",
        )
        if choice == "0":
            return
        path = ask_path("Path file media (bisa drag & drop)")
        if path is None:
            continue
        if path.is_dir():
            error("Itu sebuah folder, bukan file.")
            continue
        try:
            meta = ff.probe(path)
        except ff.ToolError as exc:
            error(str(exc))
            continue
        ui.show_media_info(meta)

        outdir = ask_outdir(path.parent)
        audio_fmt, audio_bitrate = "mp3", "192k"
        if choice in ("1", "3"):
            audio_fmt, audio_bitrate = ask_audio_profile()

        console.print()
        outputs = ui.run_local_split(
            path, outdir,
            want_audio=choice in ("1", "3"),
            want_video=choice in ("2", "3"),
            audio_fmt=audio_fmt,
            audio_bitrate=audio_bitrate,
            segment=Segment(),
            info=meta,
        )
        show_result(outputs, outdir)
        if outputs:
            _pause()


def _about(bin_dir: str) -> None:
    import platform
    try:
        import yt_dlp
        yt_ver = yt_dlp.version.__version__
    except Exception:
        yt_ver = "?"
    lines = [
        f"[bold]splitmedia[/] — split audio/video dari URL maupun file lokal",
        "",
        f"Python      : {platform.python_version()} ({platform.architecture()[0]})",
        f"Sistem      : {platform.system()} {platform.release()}",
        f"yt-dlp      : {yt_ver}",
        f"ffmpeg      : {ff.ffmpeg_exe()}",
        f"Folder bin  : {bin_dir}",
        "",
        "[dim]Dibangun dengan yt-dlp (https://github.com/yt-dlp/yt-dlp) "
        "dan FFmpeg untuk pemrosesan media.[/]",
    ]
    console.print(Panel("\n".join(lines), box=box.ROUNDED, border_style="cyan", padding=(0, 2)))
    _pause()


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if any(a in {"-h", "--help"} for a in argv):
        console.print(
            "splitmedia — menu interaktif split audio/video.\n\n"
            "Pakai:  python -m splitmedia\n"
            "        splitmedia            (bila diinstal)\n\n"
            "Semua opsi diatur lewat menu setelah aplikasi dijalankan."
        )
        return 0

    try:
        bin_dir = ff.ensure_ffmpeg()
    except ff.ToolError as exc:
        error(str(exc))
        return 2

    ui.banner()
    ui.tool_line(bin_dir)

    while True:
        choice = menu(
            [
                ("1", "Unduh dari URL (YouTube, dll) → audio / video / keduanya"),
                ("2", "File lokal → ekstrak audio & video"),
                ("3", "Split audio jadi beberapa bagian (per titik potong / durasi tetap)"),
                ("4", "Potong file lokal jadi satu segmen"),
                ("5", "Gabung file teks (.txt/.md) jadi satu file"),
                ("6", "Tentang & bantuan"),
            ],
            title="Menu utama",
        )
        if choice == "0":
            console.print("\n  [dim]Sampai jumpa![/]\n")
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
            console.print("\n  [yellow]Dibatalkan.[/]")
            continue


def _split_audio_flow() -> None:
    path = ask_path("Path file audio/video (bisa drag & drop)")
    if path is None or path.is_dir():
        return
    try:
        meta = ff.probe(path)
    except ff.ToolError as exc:
        error(str(exc))
        return
    if not meta.has_audio:
        error("File ini tidak memiliki track audio untuk dipotong.")
        return
    ui.show_media_info(meta)
    if meta.duration is None:
        warn("Durasi tidak terbaca dari file; bagian tanpa batas akhir mungkin gagal.")

    mode = menu(
        [
            ("1", "Potong pada titik tertentu — mis. 00:10:00, 00:29:59"),
            ("2", "Potong setiap durasi tetap — mis. tiap 00:29:59"),
        ],
        title="Cara split audio",
    )
    if mode == "0":
        return

    if mode == "1":
        points = ask_cut_points(meta.duration)
        ranges = ff.make_ranges_from_points(0.0, meta.duration or points[-1], points)
        if meta.duration is None:
            ranges[-1] = (ranges[-1][0], None)
    else:
        chunk = ask_chunk_length(meta.duration)
        if meta.duration is None:
            error("Durasi tidak diketahui, split berkala tidak bisa dihitung.")
            return
        ranges = ff.make_ranges(0.0, meta.duration, chunk)

    ui.show_split_preview(ranges, ff.audio_stream_ext(meta.audio))
    outdir = ask_outdir(path.parent)
    console.print()
    outputs = ui.run_audio_split(path, outdir, ranges, info=meta)
    show_result(outputs, outdir)
    if outputs:
        _pause()


def _merge_text_flow() -> None:
    files = ui.ask_text_files()
    if not files:
        return
    ordered = ui.ask_order(files)
    style = ui.ask_merge_style()
    encoding = ui.ask_output_encoding()
    outdir = ask_outdir(ordered[0].parent)
    name = ui.ask_output_name(f"gabungan {len(ordered)} file")
    output = ff.safe_output(outdir / name)
    console.print()
    result = ui.run_text_merge(ordered, output, style, encoding)
    if result is None:
        return
    ui.show_merge_preview(result)
    _pause()


def _local_clip_flow() -> None:
    path = ask_path("Path file media (bisa drag & drop)")
    if path is None or path.is_dir():
        return
    try:
        meta = ff.probe(path)
    except ff.ToolError as exc:
        error(str(exc))
        return
    ui.show_media_info(meta)
    segment = ask_segment(meta.duration)
    if segment.is_full:
        warn("Rentang kosong — seluruh durasi akan diproses.")
    kind = menu(
        [("1", "Video + audio (gabungan)"), ("2", "Audio saja"), ("3", "Video saja")],
        title="Isi potongan",
    )
    if kind == "0":
        return
    outdir = ask_outdir(path.parent)
    console.print()
    outputs = ui.run_local_clip(path, outdir, segment, kind in ("1", "2"), kind in ("1", "3"), info=meta)
    show_result(outputs, outdir)
    if outputs:
        _pause()
