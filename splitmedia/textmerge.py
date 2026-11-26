"""Gabungkan beberapa file teks menjadi satu, sesuai urutan yang dipilih."""
from __future__ import annotations

import codecs
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

TEXT_EXTS = (
    ".txt", ".md", ".markdown", ".text", ".log", ".rst", ".org",
    ".csv", ".tsv", ".srt", ".vtt", ".ini", ".conf",
)

# BOM diperiksa berurutan; UTF-32 harus didahulukan karena BOM-nya diawali BOM UTF-16.
_BOMS = (
    (codecs.BOM_UTF32_LE, "utf-32"),
    (codecs.BOM_UTF32_BE, "utf-32"),
    (codecs.BOM_UTF8, "utf-8-sig"),
    (codecs.BOM_UTF16_LE, "utf-16"),
    (codecs.BOM_UTF16_BE, "utf-16"),
)

STYLES = {
    "plain": "Langsung digabung, tanpa pemisah",
    "blank": "Satu baris kosong antar file",
    "heading": "Dengan judul nama file + garis pemisah",
}

OUTPUT_ENCODINGS = {
    "utf-8": "UTF-8 (disarankan)",
    "utf-8-sig": "UTF-8 + BOM (untuk Notepad/Excel lama)",
    "cp1252": "Windows-1252 (ANSI)",
}


class TextMergeError(RuntimeError):
    """File tidak bisa dibaca sebagai teks."""


@dataclass
class Source:
    path: Path
    encoding: str
    text: str
    chars: int
    lines: int


@dataclass
class MergeResult:
    output: Path
    sources: list[Source] = field(default_factory=list)
    skipped: list[tuple[Path, str]] = field(default_factory=list)

    @property
    def total_chars(self) -> int:
        return sum(s.chars for s in self.sources)


# ------------------------------------------------------------------- discovery

def natural_key(value: str | Path) -> list:
    """Urutkan 'bab 2' sebelum 'bab 10'."""
    parts = re.split(r"(\d+)", str(value).lower())
    return [int(p) if p.isdigit() else p for p in parts]


def list_text_files(folder: Path, include_other: bool = True) -> list[Path]:
    """Daftar file teks di satu folder, diurutkan alami (bab1, bab2, bab10)."""
    if not folder.is_dir():
        return []
    files = [
        p for p in folder.iterdir()
        if p.is_file() and (include_other or p.suffix.lower() in TEXT_EXTS)
    ]
    return sorted(files, key=natural_key)


def expand_paths(raw: str, base: Path | None = None) -> list[Path]:
    """Ubah input pengguna menjadi daftar path.

    Mendukung: satu/beberapa path (dipisah koma), pola `*`/`?`, dan nama file
    yang mengandung spasi. Karena spasi bisa bagian dari nama file, input
    dicoba utuh lebih dulu; pemisahan per-spasi hanya dipakai bila hasilnya
    memang beberapa file yang semuanya ada.
    """
    base = base or Path.cwd()
    text = raw.strip().strip('"').strip("'")
    if not text:
        return []

    whole = _resolve_token(text, base)
    if all(p.exists() for p in whole):
        return whole

    # beberapa path dipisah koma. Spasi TIDAK dipakai sebagai pemisah karena
    # banyak nama file mengandung spasi ("bab 1.txt"); memecah di spasi akan
    # salah menebak. Untuk diapit tanda kutip, semuanya dianggap satu path.
    if "," in text:
        result: list[Path] = []
        for part in (p.strip().strip('"').strip("'") for p in text.split(",")):
            if part:
                result.extend(_resolve_token(part, base))
        if any(p.exists() for p in result):
            return result

    return whole


def _resolve_token(token: str, base: Path) -> list[Path]:
    candidate = Path(os.path.expandvars(os.path.expanduser(token)))
    if not candidate.is_absolute():
        candidate = base / candidate
    if any(ch in token for ch in "*?["):
        parent = candidate.parent
        if not parent.is_dir():
            return []
        return sorted(parent.glob(candidate.name), key=natural_key)
    return [candidate]


def _split_tokens(raw: str) -> list[str]:
    """Pisahkan path yang dipisah spasi, dengan dukungan tanda kutip."""
    tokens: list[str] = []
    buf: list[str] = []
    quote: str | None = None
    for ch in raw:
        if quote:
            if ch == quote:
                quote = None
            else:
                buf.append(ch)
        elif ch in "\"'":
            quote = ch
        elif ch.isspace():
            if buf:
                tokens.append("".join(buf))
                buf.clear()
        else:
            buf.append(ch)
    if buf:
        tokens.append("".join(buf))
    return tokens


# ---------------------------------------------------------------------- reading

def read_text_file(path: Path) -> Source:
    """Baca file teks dengan deteksi encoding otomatis."""
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise TextMergeError(f"Tidak bisa membaca: {exc}") from exc

    if not data:
        return Source(path=path, encoding="utf-8", text="", chars=0, lines=0)

    text, encoding = _decode(data)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return Source(
        path=path,
        encoding=encoding,
        text=text,
        chars=len(text),
        lines=text.count("\n") + (0 if text.endswith("\n") else 1),
    )


def _decode(data: bytes) -> tuple[str, str]:
    for bom, encoding in _BOMS:
        if data.startswith(bom):
            try:
                return data.decode(encoding), encoding
            except UnicodeDecodeError:
                break
    try:
        return data.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        pass
    try:
        text = data.decode("cp1252")
        encoding = "windows-1252"
    except UnicodeDecodeError:
        text = data.decode("latin-1")
        encoding = "latin-1"
    if "\x00" in text:
        raise TextMergeError("Isinya bukan teks (mengandung byte biner).")
    return text, encoding


# ---------------------------------------------------------------------- merging

def inspect_files(paths: list[Path]) -> tuple[list[Source], list[tuple[Path, str]]]:
    """Baca semua file lebih dulu; kembalikan yang berhasil dan yang dilewati."""
    sources: list[Source] = []
    skipped: list[tuple[Path, str]] = []
    seen: set[Path] = set()
    for path in paths:
        if not path.exists():
            skipped.append((path, "file tidak ditemukan"))
            continue
        if not path.is_file():
            skipped.append((path, "bukan file"))
            continue
        try:
            key = path.resolve()
        except OSError:
            key = path
        if key in seen:
            skipped.append((path, "duplikat"))
            continue
        seen.add(key)
        try:
            sources.append(read_text_file(path))
        except TextMergeError as exc:
            skipped.append((path, str(exc)))
    return sources, skipped


def merge_files(
    paths: list[Path],
    output: Path,
    *,
    style: str = "blank",
    encoding: str = "utf-8",
    newline: str | None = None,
    title: str | None = None,
) -> MergeResult:
    """Gabungkan semua file sesuai urutan `paths` ke `output`."""
    if style not in STYLES:
        raise TextMergeError(f"Gaya gabungan tidak dikenal: {style}")
    if encoding not in OUTPUT_ENCODINGS:
        raise TextMergeError(f"Encoding output tidak dikenal: {encoding}")

    result = MergeResult(output=output)
    sources, skipped = inspect_files(paths)
    result.skipped.extend(skipped)
    chunks: list[str] = []

    for source in sources:
        if _same_file(source.path, output):
            result.skipped.append((source.path, "sama dengan file output"))
            continue
        result.sources.append(source)
        chunks.append(_render(source, style))

    if not result.sources:
        raise TextMergeError("Tidak ada file teks yang berhasil dibaca.")

    body = _join(chunks, style)
    if title:
        body = _heading(title) + body

    output.parent.mkdir(parents=True, exist_ok=True)
    nl = newline if newline is not None else ("\r\n" if os.name == "nt" else "\n")
    text = body.replace("\n", nl)
    try:
        with open(output, "w", encoding=encoding, newline="") as handle:
            handle.write(text)
    except UnicodeEncodeError as exc:
        raise TextMergeError(
            f"Ada karakter yang tidak bisa ditulis sebagai {encoding}. "
            "Pilih UTF-8 agar semua karakter tersimpan."
        ) from exc
    except OSError as exc:
        raise TextMergeError(f"Tidak bisa menulis hasil: {exc}") from exc

    return result


def _same_file(a: Path, b: Path) -> bool:
    try:
        return a.resolve() == b.resolve()
    except OSError:
        return False


def _heading(name: str) -> str:
    line = "=" * max(8, len(name) + 4)
    return f"{line}\n{name}\n{line}\n\n"


def _render(source: Source, style: str) -> str:
    text = source.text
    if style == "heading":
        return _heading(source.path.name) + text
    return text


def _join(chunks: list[str], style: str) -> str:
    cleaned = [chunk.rstrip("\n") for chunk in chunks]
    cleaned = [text for text in cleaned if text]
    if not cleaned:
        return ""
    separator = "\n" if style == "plain" else "\n\n"
    return separator.join(cleaned) + "\n"
