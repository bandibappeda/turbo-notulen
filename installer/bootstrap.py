"""Installer mandiri: pastikan semua persyaratan siap sebelum aplikasi dipakai.

Alur: periksa -> tampilkan rencana -> (persetujuan) -> pasang yang kurang ->
verifikasi -> tandai siap. Dijalankan oleh run.bat dengan Python apa pun yang
ditemukan, dan hanya memakai pustaka standar.
"""
from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import uikit as ui

PROJECT = Path(__file__).resolve().parent.parent
STATE_DIR = PROJECT / "splitmedia" / ".setup"
STATE_FILE = STATE_DIR / "state.json"
VENV = PROJECT / ".venv"
VENV_PY = VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
STAMP = STATE_DIR / "ready.json"
LOGS = STATE_DIR / "logs"

APP_ID = "splitmedia"
APP_TITLE = "splitmedia"
APP_SUB = "Download · pisah audio/video · gabung teks"
MIN_PY = (3, 9)
RECOMMENDED_PY = "3.12.10"
PY_INSTALLER = f"https://www.python.org/ftp/python/{RECOMMENDED_PY}/python-{RECOMMENDED_PY}-amd64.exe"
REQUIRED_PKGS = ("yt-dlp", "rich", "imageio-ffmpeg")
FFMPEG_URLS = (
    "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip",
    "https://github.com/GyanD/codexffmpeg/releases/download/2025-01-01-git/ffmpeg-2025-01-01-git-essentials_build.zip",
)
MIN_FFMPEG_BYTES = 10 * 1024 * 1024
UA = "splitmedia-setup/1.0 (+https://github.com/yt-dlp/yt-dlp)"


class SetupError(RuntimeError):
    """Kegagalan yang bisa dijelaskan ke pengguna."""


# ------------------------------------------------------------------ state file

@dataclass
class State:
    data: dict = field(default_factory=dict)

    @classmethod
    def load(cls) -> "State":
        try:
            return cls(json.loads(STATE_FILE.read_text(encoding="utf-8")))
        except Exception:
            return cls({})

    def save(self) -> None:
        try:
            STATE_DIR.mkdir(parents=True, exist_ok=True)
            tmp = STATE_FILE.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data, indent=2), encoding="utf-8")
            tmp.replace(STATE_FILE)
        except OSError:
            pass

    def get(self, key: str, default=None):
        return self.data.get(key, default)

    def set(self, key: str, value) -> None:
        self.data[key] = value
        self.save()


def is_ready() -> bool:
    """Cek murni (tanpa mengubah apa pun): tanda siap ada dan bagian terpasang."""
    if not STAMP.is_file():
        return False
    if not VENV_PY.is_file():
        return False
    if not ffmpeg_path().is_file():
        return False
    try:
        probe = subprocess.run(
            [str(VENV_PY), "-c", "import yt_dlp, rich, imageio_ffmpeg"],
            capture_output=True, timeout=60, creationflags=_no_window(),
        )
        return probe.returncode == 0
    except Exception:
        return False


def heal() -> bool:
    """Siapkan hal-hal yang tidak perlu unduhan, lalu laporkan apakah sudah siap.

    Dipakai saat aplikasi akan dibuka: bila hanya ffmpeg yang belum berada di
    lokasi baru (mis. sisa versi lama) atau tanda siap terhapus, semuanya
    diperbaiki tanpa mengunduh ulang.
    """
    if not VENV_PY.is_file():
        return False
    if not ffmpeg_path().is_file():
        source = _ffmpeg_from_package() or legacy_ffmpeg()
        local = shutil.which("ffmpeg")
        if source is None and local:
            source = Path(local)
        if source is None or not _place_ffmpeg(source):
            return False
    if not all(item.ok for item in collect_findings()):
        return False
    if not STAMP.is_file():
        verify_and_stamp()
    return True


# ------------------------------------------------------------------- findings

@dataclass
class Finding:
    key: str
    name: str
    ok: bool
    detail: str
    version: str = ""
    required: bool = True


def _run(cmd: list[str], timeout: float = 20) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, errors="replace",
                          timeout=timeout, creationflags=_no_window())


def _no_window() -> int:
    return 0x08000000 if os.name == "nt" else 0


# ---------------------------------------------------------------------- python

def find_pythons() -> list[tuple[tuple[int, int, int], str, str]]:
    """Kumpulkan semua Python yang bisa dipakai membuat venv."""
    found: list[tuple[tuple[int, int, int], str, str]] = []
    seen: set[str] = set()

    def probe(exe: str) -> None:
        try:
            proc = _run([exe, "-c",
                         "import sys,platform;print('%d.%d.%d'%sys.version_info[:3]);"
                         "print(platform.architecture()[0])"])
        except Exception:
            return
        if proc.returncode != 0:
            return
        lines = proc.stdout.strip().splitlines()
        if len(lines) < 2:
            return
        try:
            version = tuple(int(x) for x in lines[0].split("."))
        except ValueError:
            return
        if len(version) != 3:
            return
        if "64" not in lines[1]:
            return
        try:
            real = str(Path(exe).resolve())
        except OSError:
            real = exe
        if real.lower() in seen:
            return
        seen.add(real.lower())
        # hanya versi 64-bit dan >= MIN_PY yang boleh dipakai membuat venv
        if version >= (*MIN_PY, 0) and "-32" not in real:
            found.append((version, exe, lines[0]))

    # 1. launcher resmi Windows (menangani semua versi terpasang)
    if os.name == "nt":
        try:
            proc = _run(["py", "-0p"], timeout=15)
            for line in (proc.stdout or "").splitlines():
                m = re.search(r"([A-Za-z]:\\[^\n]*?python\.exe)", line)
                if m:
                    probe(m.group(1))
        except Exception:
            pass
        for name in ("py",):
            exe = shutil.which(name)
            if exe:
                for args in (["-3.12"], ["-3.11"], ["-3.13"], ["-3"], []):
                    try:
                        proc = _run([exe, *args, "-c", "import sys;print(sys.executable)"])
                        if proc.returncode == 0:
                            target = proc.stdout.strip().splitlines()
                            if target:
                                probe(target[-1])
                    except Exception:
                        continue

    # 2. python di PATH
    for name in ("python", "python3"):
        exe = shutil.which(name)
        if exe:
            probe(exe)

    # 3. lokasi instalasi umum
    candidates: list[Path] = []
    for root in (Path.home() / "AppData/Local/Programs/Python",
                 Path("C:/Program Files"), Path("C:/Program Files (x86)"), Path("C:/")):
        try:
            if root.is_dir():
                candidates.extend(sorted(root.glob("Python3*")))
        except OSError:
            pass
    for folder in candidates:
        exe = folder / ("python.exe" if os.name == "nt" else "bin/python3")
        if exe.is_file():
            probe(str(exe))

    # pilih versi terbaru yang tersedia
    found.sort(key=lambda item: item[0], reverse=True)
    return found


def best_python() -> tuple[str, str] | None:
    """Utamakan Python 3.10-3.12 (paling stabil), selain itu versi terbaru.

    Versi di bawah 3.9 tidak dipakai karena tidak memenuhi syarat minimum.
    """
    found = [item for item in find_pythons() if item[0] >= (*MIN_PY, 0) and item[0] >= (3, 9)]
    if not found:
        return None
    preferred = [item for item in found if (3, 10) <= item[0] <= (3, 12)]
    pick = preferred[0] if preferred else found[0]
    return pick[1], pick[2]


# ---------------------------------------------------------------------- checks

def check_python() -> Finding:
    best = best_python()
    if best:
        return Finding("python", "Python", True, os.path.basename(best[0]), version=best[1])
    return Finding("python", "Python", False, f"belum ada (butuh {MIN_PY[0]}.{MIN_PY[1]}+)")


def _venv_packages() -> set[str]:
    if not VENV_PY.is_file():
        return set()
    try:
        proc = _run([str(VENV_PY), "-c",
                     "import importlib.metadata as m;"
                     "print('\\n'.join(sorted({d.metadata['Name'].lower() for d in m.distributions() if d.metadata['Name']})))"],
                    timeout=60)
        if proc.returncode != 0:
            return set()
        return {line.strip() for line in proc.stdout.splitlines() if line.strip()}
    except Exception:
        return set()


def check_venv() -> Finding:
    if VENV_PY.is_file():
        try:
            proc = _run([str(VENV_PY), "-c", "import sys;print('%d.%d.%d'%sys.version_info[:3])"])
            if proc.returncode == 0:
                return Finding("venv", "Lingkungan aplikasi", True, ".venv siap",
                               version=proc.stdout.strip())
        except Exception:
            pass
        return Finding("venv", "Lingkungan aplikasi", False, "rusak, akan dibuat ulang")
    return Finding("venv", "Lingkungan aplikasi", False, "belum dibuat")


def check_packages() -> Finding:
    installed = _venv_packages()
    missing = [pkg for pkg in REQUIRED_PKGS if pkg.lower() not in installed]
    if not missing:
        return Finding("packages", "Paket Python", True, ", ".join(REQUIRED_PKGS))
    return Finding("packages", "Paket Python", False, "kurang: " + ", ".join(missing))


def ffmpeg_path() -> Path:
    return STATE_DIR / "bin" / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")


def _probe_ffmpeg(path: Path) -> str | None:
    try:
        proc = _run([str(path), "-hide_banner", "-version"], timeout=25)
        if proc.returncode != 0:
            return None
        m = re.search(r"ffmpeg version (\S+)", proc.stdout or "")
        return m.group(1) if m else "?"
    except Exception:
        return None


def legacy_ffmpeg() -> Path | None:
    """Versi lama yang disiapkan versi aplikasi sebelumnya (%LOCALAPPDATA%)."""
    base = os.environ.get("LOCALAPPDATA")
    if not base:
        return None
    candidate = Path(base) / APP_ID / "bin" / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")
    if candidate.is_file() and _probe_ffmpeg(candidate):
        return candidate
    return None


def check_ffmpeg(packages_ready: bool = True) -> Finding:
    target = ffmpeg_path()
    if target.is_file():
        if target.stat().st_size < 100_000:
            return Finding("ffmpeg", "FFmpeg", False, "file rusak, akan disiapkan ulang")
        version = _probe_ffmpeg(target)
        if version:
            return Finding("ffmpeg", "FFmpeg", True, "siap", version=version)
        return Finding("ffmpeg", "FFmpeg", False, "tidak bisa dijalankan, akan disiapkan ulang")
    # Salinan ffmpeg di lokasi lain (PATH / sisa versi lama) hanya dipakai sebagai
    # sumber untuk disalin, bukan dianggap "siap": aplikasi harus mandiri agar
    # bisa dipindah ke komputer lain tanpa bergantung pada isi sistem.
    if legacy_ffmpeg() or _ffmpeg_from_package():
        return Finding("ffmpeg", "FFmpeg", False, "disalin otomatis (tanpa unduh)")
    local = shutil.which("ffmpeg")
    if local and Path(local).name.lower().startswith("ffmpeg"):
        return Finding("ffmpeg", "FFmpeg", False, "disalin dari sistem (tanpa unduh)")
    return Finding("ffmpeg", "FFmpeg", False, "akan disiapkan otomatis")


def collect_findings() -> list[Finding]:
    packages = check_packages()
    return [check_python(), check_venv(), packages, check_ffmpeg(packages.ok)]


# ------------------------------------------------------------------- display

def show_banner() -> None:
    ui.banner(APP_TITLE, APP_SUB, "Penyiapan pertama · otomatis & sekali saja")


def show_steps(findings: list[Finding]) -> None:
    ui.section("Memeriksa persyaratan")
    for item in findings:
        detail = item.detail
        if item.version and item.ok:
            detail = item.version
        ui.line("ok" if item.ok else "no", f"{item.name:<22}", detail,
                mark_style="green" if item.ok else "yellow")
    missing = [f for f in findings if not f.ok]
    ui.w()
    if not missing:
        ui.line("ok", "Semua persyaratan siap.", "", mark_style="green")
        return
    _, size = estimate_download(findings)
    note = "izin diminta sebelum mengunduh" if size else "sebagian hanya perlu disalin"
    ui.line("warn", f"{len(missing)} bagian perlu disiapkan.", note, mark_style="yellow")


def estimate_download(findings: list[Finding]) -> tuple[int, float]:
    """Perkiraan jumlah & ukuran unduhan (byte).

    ffmpeg tidak dihitung terpisah karena sudah ikut di dalam paket
    imageio-ffmpeg (yang masuk hitungan paket Python).
    """
    by_key = {f.key: f for f in findings}
    jobs = 0
    total = 0
    if not by_key["python"].ok:
        jobs += 1
        total += 26 * 1024 * 1024
    if not by_key["venv"].ok:
        jobs += 1
    if not by_key["packages"].ok:
        jobs += 1
        total += 75 * 1024 * 1024   # yt-dlp + rich + imageio-ffmpeg (ffmpeg di dalamnya)
    if not by_key["ffmpeg"].ok:
        jobs += 1
    return jobs, total


# ------------------------------------------------------------------- installers

def _log(name: str) -> Path:
    LOGS.mkdir(parents=True, exist_ok=True)
    return LOGS / f"{name}-{datetime.now():%Y%m%d-%H%M%S}.log"


def _clean_env() -> dict:
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)
    env["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def install_python() -> str:
    """Unduh dan pasang Python (perlu izin pengguna, terlihat jelas)."""
    ui.w()
    ui.box([
        ("Python belum terpasang di komputer ini.", "yellow"),
        "",
        f"Installer akan mengunduh Python {RECOMMENDED_PY} (64-bit) dari python.org",
        "dan memasangnya hanya untuk pengguna ini (tanpa akses administrator).",
        "",
        ("Tidak ada perubahan pada Python lain yang sudah terpasang.", "gray"),
    ], title="Perlu pasang Python")
    ui.w("  " + ui.c("Installer resmi: ", "gray") + ui.c(PY_INSTALLER, "gray"))
    if not ui.confirm("Lanjutkan memasang Python?", default=True):
        raise SetupError("Pemasangan dibatalkan. Python dibutuhkan untuk menjalankan aplikasi.")

    setup = STATE_DIR / "cache" / f"python-{RECOMMENDED_PY}-amd64.exe"
    if not (setup.is_file() and setup.stat().st_size > 20 * 1024 * 1024):
        download(PY_INSTALLER, setup, "Mengunduh Python")

    ui.line("warn", "Menjalankan installer Python…", "jendela progres muncul sebentar",
            mark_style="yellow")
    log = _log("python")
    proc = subprocess.run(
        [str(setup), "/quiet", "InstallAllUsers=0", "PrependPath=1",
         "Include_test=0", "Include_launcher=1", "Include_pip=1",
         f"Log={log}"],
        creationflags=_no_window(),
    )
    if proc.returncode not in (0, 3010):
        raise SetupError(
            "Pemasangan Python gagal. Jalankan installer ini secara manual:\n"
            f"  {setup}\n  lalu buka aplikasi lagi."
        )

    # installer menyembunyikan Python dari PATH proses yang sedang jalan
    deadline = time.time() + 90
    while time.time() < deadline:
        best = best_python()
        if best:
            ui.line("ok", "Python terpasang", best[1])
            return best[0]
        time.sleep(2)
    raise SetupError("Python terpasang tetapi tidak terdeteksi. Tutup dan buka ulang aplikasi.")


def create_venv(python_exe: str) -> None:
    with ui.Live("Membuat lingkungan aplikasi", detail=".venv") as live:
        if VENV.exists():
            shutil.rmtree(VENV, ignore_errors=True)
        log = _log("venv")
        proc = subprocess.run(
            [python_exe, "-m", "venv", str(VENV)],
            capture_output=True, text=True, errors="replace", env=_clean_env(),
            creationflags=_no_window(),
        )
        if proc.returncode != 0:
            log.write_text(proc.stdout + "\n" + proc.stderr, encoding="utf-8")
            raise SetupError(f"Gagal membuat .venv. Detail: {log}")
        if not VENV_PY.is_file():
            raise SetupError("Lingkungan dibuat tetapi python tidak ditemukan di dalamnya.")
        live.finish(text="Lingkungan aplikasi siap")


_SIZE_RE = re.compile(r"\(([\d.]+)\s*(kB|MB|GB)\)", re.IGNORECASE)
_NAME_RE = re.compile(r"^\s*(?:Collecting|Downloading|Using cached)\s+([\w.\-\[\]]+)")
_PIP_STAGE_RE = re.compile(r"(Collecting|Downloading|Installing collected|Building wheel)")


def install_packages(venv_python: str | None = None) -> None:
    """Pasang paket dengan progres unduhan yang ditakar dari baris pip."""
    requirements = ["yt-dlp", "rich", "imageio-ffmpeg"]
    log = _log("pip")
    python_exe = venv_python or str(VENV_PY)
    with ui.Live("Mengunduh & memasang paket", detail="menghubungi PyPI…") as live:
        proc = subprocess.Popen(
            [python_exe, "-m", "pip", "install", "--upgrade", "--no-input",
             "--disable-pip-version-check", "--progress-bar", "off", *requirements],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            errors="replace", env=_clean_env(), creationflags=_no_window(), bufsize=1,
        )
        assert proc.stdout is not None
        tail: list[str] = []
        total_bytes = 0.0      # akumulasi ukuran semua paket yang akan diunduh
        done_bytes = 0.0       # paket yang sudah selesai diunduh
        current_size = 0.0     # ukuran paket yang sedang diunduh
        fraction = 0.0         # kemajuan paket berjalan (0..1), bila terbaca
        try:
            for raw in proc.stdout:
                line = raw.rstrip("\n")
                tail.append(line)
                if len(tail) > 8:
                    tail.pop(0)

                stage = _PIP_STAGE_RE.search(line)
                name = _NAME_RE.match(line)

                if stage and stage.group(1) == "Downloading":
                    current_size = 0.0
                    fraction = 0.0
                    size = _SIZE_RE.search(line)
                    if size:
                        current_size = float(size.group(1)) * {
                            "kb": 1e3, "mb": 1e6, "gb": 1e9,
                        }[size.group(2).lower()]
                        total_bytes += current_size
                        live.update(total=int(total_bytes))
                    if name:
                        live.update(detail=f"mengunduh {name.group(1)[:26]}")
                elif stage and stage.group(1) == "Collecting":
                    done_bytes += current_size
                    current_size = 0.0
                    fraction = 0.0
                    live.update(done=int(done_bytes))
                    if name:
                        live.update(detail=f"menyiapkan {name.group(1)[:26]}")
                elif stage:
                    done_bytes += current_size
                    current_size = 0.0
                    fraction = 0.0
                    if total_bytes:
                        live.update(done=int(total_bytes), total=int(total_bytes))
                    live.update(detail="memasang paket…")
                else:
                    # pip 23+ mencetak "12.3/45.6 MB" untuk paket yang sedang berjalan
                    bar = re.search(r"([\d.]+)\s*/\s*(([\d.]+))\s*(kB|MB|GB)", line,
                                    re.IGNORECASE)
                    if bar:
                        fraction = min(1.0, float(bar.group(1)) / max(float(bar.group(3)), 1e-9))
                        live.update(done=int(done_bytes + fraction * current_size))
            code = proc.wait()
        finally:
            if proc.poll() is None:
                proc.kill()
        log.write_text("\n".join(tail), encoding="utf-8")
        if code != 0:
            raise SetupError(
                "Gagal memasang paket Python. Periksa koneksi internet.\n"
                f"  Log: {log}\n  Pesan: {tail[-1] if tail else '-'}"
            )
        missing = [p for p in REQUIRED_PKGS if p.lower() not in _venv_packages()]
        if missing:
            raise SetupError(f"Paket belum terpasang: {', '.join(missing)}")
        live.finish(text="Paket Python terpasang")


# --------------------------------------------------------------------- download

def download(url: str, dest: Path, label: str, *, min_bytes: int = 1000) -> Path:
    """Unduh dengan progress bar, dukungan resume, dan pesan error ramah."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_suffix(dest.suffix + ".part")
    attempt = 0
    last_error: Exception | None = None
    while attempt < 3:
        attempt += 1
        try:
            _download_once(url, part, label, min_bytes=min_bytes)
            if dest.exists():
                dest.unlink()
            part.replace(dest)
            return dest
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_error = exc
            if attempt < 3:
                ui.w("  " + ui.c(f"Unduhan terputus ({exc}). Mencoba lagi ({attempt}/3)…", "yellow"))
                time.sleep(2)
        except SetupError:
            raise
    raise SetupError(
        f"Gagal mengunduh {label} setelah 3 percobaan: {last_error}\n"
        f"  URL: {url}\n  Periksa koneksi internet lalu jalankan ulang."
    )


def _download_once(url: str, part: Path, label: str, *, min_bytes: int) -> None:
    resume = part.stat().st_size if part.is_file() else 0
    request = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "*/*",
        "Range": f"bytes={resume}-" if resume else "bytes=0-",
    })
    try:
        response = urllib.request.urlopen(request, timeout=45)
    except urllib.error.HTTPError as exc:
        if exc.code == 416 and resume:  # sudah lengkap
            if part.stat().st_size >= min_bytes:
                return
        if exc.code in (403, 404):
            raise SetupError(f"Tautan unduhan tidak tersedia (HTTP {exc.code}).") from exc
        raise
    with response:
        status = response.status
        if resume and status != 206:
            resume = 0
        length = response.headers.get("Content-Length")
        total = (int(length) + resume) if length and length.isdigit() else None
        mode = "ab" if resume else "wb"
        done = resume
        last = 0.0
        with ui.Live(label, total=total) as live:
            if total:
                live.update(done=done)
            with open(part, mode) as handle:
                while True:
                    chunk = response.read(262144)
                    if not chunk:
                        break
                    handle.write(chunk)
                    done += len(chunk)
                    now = time.time()
                    if now - last > 0.1:
                        live.update(done=done, total=total)
                        last = now
            live.finish(total=done)
    if part.stat().st_size < min_bytes:
        part.unlink(missing_ok=True)
        raise SetupError(
            f"File {label} yang terunduh terlalu kecil / rusak. Coba lagi nanti."
        )


# ---------------------------------------------------------------------- ffmpeg

def _find_in_tree(root: Path, name: str) -> Path | None:
    for candidate in root.rglob(name):
        if candidate.is_file():
            return candidate
    return None


def _ffmpeg_from_package(python_exe: str | None = None) -> Path | None:
    """ffmpeg bawaan paket imageio-ffmpeg (diunduh sekalian saat pip install)."""
    try:
        proc = _run([python_exe or str(VENV_PY), "-c",
                     "import imageio_ffmpeg;print(imageio_ffmpeg.get_ffmpeg_exe())"],
                    timeout=90)
        if proc.returncode == 0:
            candidate = Path(proc.stdout.strip().splitlines()[-1])
            if candidate.is_file() and _probe_ffmpeg(candidate):
                return candidate
    except Exception:
        pass
    return None


def _place_ffmpeg(exe: Path) -> bool:
    """Salin `exe` sebagai ffmpeg.exe (+ffprobe bila ada) ke folder bin aplikasi.

    Nama asal bisa apa saja (mis. imageio-ffmpeg memakai
    `ffmpeg-win-x86_64-v9.0.2.exe`), jadi file sumber selalu disalin dengan
    nama tujuan yang tetap.
    """
    target_dir = ffmpeg_path().parent
    target_dir.mkdir(parents=True, exist_ok=True)
    try:
        if not exe.is_file():
            return False
        if exe.resolve() != ffmpeg_path().resolve():
            shutil.copy2(exe, ffmpeg_path())
        probe_src = exe.with_name(exe.name.replace("ffmpeg", "ffprobe"))
        if probe_src.is_file():
            probe_dst = target_dir / ("ffprobe.exe" if os.name == "nt" else "ffprobe")
            if probe_src.resolve() != probe_dst.resolve():
                shutil.copy2(probe_src, probe_dst)
    except OSError as exc:
        ui.w("  " + ui.c(f"Gagal menyalin FFmpeg: {exc}", "red"))
        return False
    return ffmpeg_path().is_file() and bool(_probe_ffmpeg(ffmpeg_path()))


def install_ffmpeg(venv_python: str | None = None) -> None:
    target = ffmpeg_path()
    # 1. cara tercepat: paket imageio-ffmpeg sudah membawa ffmpeg lengkap,
    #    jadi tidak perlu mengunduh ~120 MB lagi dari internet.
    with ui.Live("Menyiapkan FFmpeg", detail="memakai paket Python") as live:
        packaged = _ffmpeg_from_package(venv_python)
        if packaged and _place_ffmpeg(packaged):
            live.finish(text="FFmpeg siap (dari paket imageio-ffmpeg)")
            return

        # 2. pakai ulang unduhan versi aplikasi sebelumnya bila ada
        old = legacy_ffmpeg()
        if old is not None and old.parent != target.parent and _place_ffmpeg(old):
            live.finish(text="FFmpeg siap (memakai unduhan sebelumnya)")
            return

        # 3. pakai ffmpeg yang sudah terpasang di sistem
        local = shutil.which("ffmpeg")
        if local and _place_ffmpeg(Path(local)):
            live.finish(text="FFmpeg siap (disalin dari sistem)")
            return
        live.finish(text="FFmpeg dari paket tidak tersedia", ok=False, note="lanjut unduh")
    ui.w("  " + ui.c("Mengunduh FFmpeg dari internet…", "gray"))

    archive = STATE_DIR / "cache" / "ffmpeg.zip"
    errors: list[str] = []
    for url in FFMPEG_URLS:
        try:
            if not (archive.is_file() and archive.stat().st_size > MIN_FFMPEG_BYTES):
                download(url, archive, "Mengunduh FFmpeg", min_bytes=MIN_FFMPEG_BYTES)
            with ui.Live("Memasang FFmpeg", detail="mengekstrak arsip") as live:
                extract = STATE_DIR / "cache" / "ffmpeg-extract"
                shutil.rmtree(extract, ignore_errors=True)
                extract.mkdir(parents=True, exist_ok=True)
                try:
                    with zipfile.ZipFile(archive) as zf:
                        bad = zf.testzip()
                        if bad:
                            raise SetupError(f"Arsip FFmpeg rusak ({bad}).")
                        zf.extractall(extract)
                except zipfile.BadZipFile as exc:
                    archive.unlink(missing_ok=True)
                    raise SetupError("Arsip FFmpeg tidak valid.") from exc
                exe = _find_in_tree(extract, "ffmpeg.exe" if os.name == "nt" else "ffmpeg")
                if exe is None:
                    raise SetupError("ffmpeg tidak ditemukan di dalam arsip.")
                _place_ffmpeg(exe)
                live.finish(text="FFmpeg terpasang")
            shutil.rmtree(extract, ignore_errors=True)
            archive.unlink(missing_ok=True)
            if _probe_ffmpeg(target):
                return
            errors.append("ffmpeg tidak bisa dijalankan setelah dipasang")
        except SetupError as exc:
            errors.append(str(exc))
            continue
    raise SetupError("FFmpeg gagal disiapkan:\n  " + "\n  ".join(errors))


# ------------------------------------------------------------------- execution

@dataclass
class Step:
    key: str
    label: str
    run: "callable"
    needs: tuple[str, ...] = ()   # nilai yang harus sudah ada sebelum langkah ini


def _resolve(step: Step, values: dict[str, str]) -> list[str]:
    """Ambil nilai `needs` dari hasil langkah sebelumnya.

    Bila langkah penghasilnya tidak ada di rencana (mis. venv sudah dibuat
    pada percobaan sebelumnya), nilainya dicari ulang dari kondisi saat ini.
    """
    args: list[str] = []
    for need in step.needs:
        if need in values:
            args.append(values[need])
            continue
        if need == "python":
            found = best_python()
            if found:
                values["python"] = found[0]
                args.append(found[0])
                continue
        if need == "venv" and VENV_PY.is_file():
            values["venv"] = str(VENV_PY)
            args.append(str(VENV_PY))
            continue
        raise SetupError(f"Bagian prasyarat '{need}' belum tersedia.")
    return args


def build_plan(findings: list[Finding]) -> list[Step]:
    by_key = {f.key: f for f in findings}
    steps: list[Step] = []
    if not by_key["python"].ok:
        steps.append(Step("python", "Pasang Python", install_python))
    if not by_key["venv"].ok or not by_key["python"].ok:
        steps.append(Step("venv", "Siapkan lingkungan aplikasi", create_venv,
                          needs=("python",)))
    if not by_key["packages"].ok or not by_key["venv"].ok:
        steps.append(Step("packages", "Pasang paket Python", install_packages,
                          needs=("venv",)))
    if not by_key["ffmpeg"].ok:
        steps.append(Step("ffmpeg", "Siapkan FFmpeg", install_ffmpeg, needs=("venv",)))
    return steps


def run_plan(steps: list[Step]) -> None:
    ui.section(f"Memasang {len(steps)} bagian")
    ui.w()
    values: dict[str, str] = {}
    # Python mungkin sudah ada sehingga langkah pemasangannya tidak masuk rencana;
    # tetap sediakan path-nya untuk langkah yang membutuhkannya.
    existing = best_python()
    if existing:
        values["python"] = existing[0]
    for index, step in enumerate(steps, 1):
        ui.w("  " + ui.c(f"[{index}/{len(steps)}] ", "bold")
             + ui.c(step.label, "bold"))
        try:
            result = step.run(*_resolve(step, values))
            if isinstance(result, str):
                values[step.key] = result
        except SetupError:
            raise
        except KeyboardInterrupt:
            ui.w()
            raise SetupError("Dibatalkan oleh pengguna.") from None
        except Exception as exc:  # noqa: BLE001 - tampilkan rapi, jangan traceback
            raise SetupError(f"{step.label} gagal: {type(exc).__name__}: {exc}") from exc
        ui.w()


def verify_and_stamp() -> None:
    ui.section("Memeriksa ulang")
    findings = collect_findings()
    for item in findings:
        ui.line("ok" if item.ok else "no", f"{item.name:<22}",
                item.version or item.detail,
                mark_style="green" if item.ok else "red")
    broken = [f for f in findings if not f.ok]
    if broken:
        raise SetupError("Masih ada yang belum siap: " + ", ".join(f.name for f in broken))

    stamp = {
        "ready": True,
        "installed_at": datetime.now().isoformat(timespec="seconds"),
        "python": findings[0].version,
        "venv_python": findings[1].version,
        "packages": REQUIRED_PKGS,
        "ffmpeg": findings[3].version,
        "ffmpeg_path": str(ffmpeg_path()),
        "platform": f"{platform.system()} {platform.release()}",
    }
    try:
        STAMP.write_text(json.dumps(stamp, indent=2), encoding="utf-8")
    except OSError:
        pass


# ------------------------------------------------------------------------ main

def run(force: bool = False) -> int:
    ui.init()
    show_banner()

    state = State.load()
    if not force and heal():
        ui.section("Memeriksa persyaratan")
        for item in collect_findings():
            ui.line("ok", f"{item.name:<22}", item.version or item.detail)
        ui.w()
        ui.line("ok", "Semua sudah siap — aplikasi bisa langsung dipakai.", "",
                mark_style="green")
        installed = state.get("installed_at")
        if installed:
            ui.w("  " + ui.c(f"Terpasang sejak {installed}", "gray"))
        ui.w()
        ui.countdown(0.6, "Membuka aplikasi…")
        return 0

    findings = collect_findings()
    show_steps(findings)
    steps = build_plan(findings)

    if not steps:
        verify_and_stamp()
        ui.w()
        ui.line("ok", "Semua siap.", "", mark_style="green")
        return 0

    jobs, size = estimate_download(findings)
    download_line = (
        "Perkiraan unduhan: " + ui.c(f"±{ui.human(size)}", "yellow")
        if size else "Tidak perlu unduh — semua sumber sudah tersedia."
    )
    ui.w()
    ui.box([
        ("Penyiapan diperlukan.", "bold"),
        "",
        f"Installer akan menyiapkan {jobs} bagian secara otomatis.",
        download_line,
        "",
        ("Bisa dibatalkan kapan saja dengan Ctrl+C tanpa mengubah file Anda.", "gray"),
    ], title="Rencana penyiapan")
    ui.w()
    if not ui.confirm("Mulai penyiapan sekarang?", default=True):
        ui.w()
        ui.w("  " + ui.c("Dibatalkan. Jalankan aplikasi lagi saat siap menyiapkan.", "yellow"))
        return 1

    started = time.time()
    try:
        run_plan(steps)
        verify_and_stamp()
    except SetupError as exc:
        ui.w()
        ui.box([("Penyiapan belum berhasil", "bold red"), "",
                *[(line, "") for line in str(exc).splitlines()]],
               title="Gagal", color="red")
        ui.w()
        ui.box([
            ("Apa yang bisa dicoba:", "bold"),
            "",
            "1. Jalankan ulang run.bat — bagian yang sudah selesai tidak diunduh lagi.",
            "2. Pastikan koneksi internet aktif dan tidak diblokir firewall.",
            "3. Bila masih gagal, lihat berkas log di folder di bawah ini.",
            "",
            (f"Log: {LOGS}", "gray"),
        ], title="Langkah berikutnya", color="yellow")
        return 2
    except KeyboardInterrupt:
        ui.w()
        ui.w("  " + ui.c("Dibatalkan. Progres yang sudah ada akan dipakai saat dijalankan lagi.", "yellow"))
        return 130

    elapsed = ui.duration(time.time() - started)
    ui.w()
    ui.box([
        ("Selesai! Semua persyaratan sudah siap.", "bold green"),
        "",
        f"Total waktu: {elapsed}",
        "",
        ("Berikutnya: aplikasi terbuka otomatis.", "gray"),
    ], title="Berhasil", color="green")
    return 0


def main(argv: list[str]) -> int:
    force = "--force" in argv or "--reinstall" in argv
    try:
        return run(force=force)
    except KeyboardInterrupt:
        print()
        return 130


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
