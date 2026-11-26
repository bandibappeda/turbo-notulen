"""UI terminal ringan untuk installer (hanya memakai stdlib).

Installer harus bisa berjalan sebelum paket seperti rich terpasang, jadi seluruh
tampilan di sini dibuat manual dengan ANSI escape sequence. Bila terminal tidak
mendukung (mis. output dialihkan ke file), animasi otomatis dimatikan dan teks
tetap terbaca.
"""
from __future__ import annotations

import os
import re
import shutil
import sys
import threading
import time

RESET = "\033[0m"
BOLD = "1"
DIM = "2"

_CODES = {
    "black": "30", "red": "31", "green": "32", "yellow": "33",
    "blue": "34", "magenta": "35", "cyan": "36", "white": "37", "gray": "90",
}

USE_COLOR = False
ANIMATE = False
WIDTH = 64
_READY = False

MARKS = {"ok": "✔", "no": "✘", "cur": "●", "todo": "○", "warn": "!"}


# ------------------------------------------------------------------ bootstrap

def init() -> None:
    """Siapkan konsol: VT, UTF-8, ukuran, dan deteksi TTY."""
    global USE_COLOR, ANIMATE, WIDTH, _READY
    if _READY:
        return
    _READY = True
    _enable_vt()
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    try:
        tty = sys.stdout.isatty()
    except Exception:
        tty = False
    USE_COLOR = tty and os.environ.get("NO_COLOR") is None
    ANIMATE = USE_COLOR
    try:
        cols = shutil.get_terminal_size((80, 25)).columns
    except Exception:
        cols = 80
    WIDTH = max(54, min(76, cols - 4))


def _enable_vt() -> None:
    if os.name != "nt":
        return
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)
        mode = ctypes.c_uint32()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return
        kernel32.SetConsoleMode(handle, mode.value | 0x0004)
    except Exception:
        pass


# --------------------------------------------------------------------- ansi

def c(text: str, *styles: str) -> str:
    """Beri warna/gaya pada teks (diabaikan bila konsol tanpa warna)."""
    if not USE_COLOR or not styles:
        return text
    codes = []
    for style in styles:
        if style in _CODES:
            codes.append(_CODES[style])
        elif style in (BOLD, DIM):
            codes.append(style)
    if not codes:
        return text
    return "\033[" + ";".join(codes) + "m" + text + RESET


_ANSI_RE = re.compile(r"\033\[[0-9;]*m")


def vlen(text: str) -> int:
    return len(_ANSI_RE.sub("", text))


def w(text: str = "", end: str = "\n") -> None:
    sys.stdout.write(text + end)
    sys.stdout.flush()


def clear_line() -> None:
    sys.stdout.write("\r\033[K")
    sys.stdout.flush()


# ------------------------------------------------------------------ primitives

def human(n: float | None) -> str:
    if n is None:
        return "?"
    n = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1000 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1000
    return f"{n:.1f} TB"


def duration(seconds: float | None) -> str:
    if seconds is None or seconds != seconds or seconds < 0:
        return "?"
    seconds = int(round(seconds))
    if seconds < 60:
        return f"{seconds} dtk"
    return f"{seconds // 60} mnt {seconds % 60:02d} dtk"


def rule(char: str = "─", color: str = "gray") -> None:
    w(c("  " + char * (WIDTH - 2), color))


def _wrap(text: str, width: int) -> list[str]:
    if vlen(text) <= width:
        return [text]
    words = text.split(" ")
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip() if current else word
        if len(candidate) <= width:
            current = candidate
        else:
            if current:
                lines.append(current)
            while len(word) > width:
                lines.append(word[:width])
                word = word[width:]
            current = word
    if current:
        lines.append(current)
    return lines or [""]


def banner(title: str, subtitle: str = "", hint: str = "") -> None:
    inner = WIDTH - 4
    rows: list[tuple[str, str]] = []
    rows.append(((" " * max(0, (inner - len(title)) // 2)) + title, "bold cyan"))
    if subtitle:
        gap = max(0, (inner - len(subtitle)) // 2)
        rows.append(((" " * gap) + subtitle, "white"))
    if hint:
        gap = max(0, (inner - len(hint)) // 2)
        rows.append(("", ""))
        rows.append(((" " * gap) + hint, "gray"))
    w(c("  ╭" + "─" * (WIDTH - 2) + "╮", "cyan"))
    w(c("  │", "cyan") + " " * inner + c("│", "cyan"))
    for text, style in rows:
        if not text:
            w(c("  │", "cyan") + " " * inner + c("│", "cyan"))
            continue
        padded = text + " " * max(0, inner - vlen(text))
        w(c("  │", "cyan") + c(padded, *style.split()) + c("│", "cyan"))
    w(c("  │", "cyan") + " " * inner + c("│", "cyan"))
    w(c("  ╰" + "─" * (WIDTH - 2) + "╯", "cyan"))
    w()


def box(rows: list[tuple[str, str]] | list[str], title: str | None = None,
        color: str = "cyan") -> None:
    """Kotak dengan judul; tiap baris boleh (teks, gaya)."""
    inner = WIDTH - 4
    if title:
        t = f" {title} "
        pad = WIDTH - 2 - len(t) - 2
        top = "╭─" + t + "─" * max(0, pad) + "─╮" if pad >= 0 else "╭" + "─" * (WIDTH - 2) + "╮"
    else:
        top = "╭" + "─" * (WIDTH - 2) + "╮"
    w(c("  " + top, color))
    for row in rows:
        if isinstance(row, tuple):
            text, style = row
        else:
            text, style = row, ""
        if text == "":
            w(c("  │", color) + " " * inner + c("│", color))
            continue
        for piece in _wrap(text, inner - 1):
            padded = " " + piece + " " * max(0, inner - 1 - vlen(piece))
            if style:
                w(c("  │", color) + c(padded, *style.split()) + c("│", color))
            else:
                w(c("  │", color) + padded + c("│", color))
    w(c("  ╰" + "─" * (WIDTH - 2) + "╯", color))


def line(mark: str, text: str, note: str = "", mark_style: str = "green") -> None:
    head = c(f"  {MARKS.get(mark, mark)}", mark_style)
    if not note:
        w(head + " " + text)
        return
    # jarak minimal dua spasi agar catatan tidak menempel pada teks panjang
    pad = max(2, 44 - vlen(text))
    w(head + " " + text + " " * pad + c(note, "gray"))


def section(text: str) -> None:
    w()
    w("  " + c(text, "bold", "cyan"))


# --------------------------------------------------------------------- lives

class Live:
    """Satu baris dinamis: spinner, marquee, atau progress bar."""

    _active_lock = threading.Lock()
    _active: "Live | None" = None

    def __init__(self, text: str = "", total: float | None = None,
                 detail: str = "", indent: int = 4) -> None:
        self.text = text
        self.total = total
        self.done = 0.0
        self.detail = detail
        self.indent = indent
        self.bar_w = min(24, max(12, WIDTH - 46))
        self._samples: list[tuple[float, float]] = []
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._t0 = time.time()
        self._stopped = False

    # ---- lifecycle
    def start(self) -> "Live":
        with Live._active_lock:
            if Live._active is not None and Live._active is not self:
                Live._active.stop()
            Live._active = self
        if not ANIMATE:
            w(f"{' ' * self.indent}{self.text}…")
            return self
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        return self

    def __enter__(self) -> "Live":
        return self.start()

    def __exit__(self, *exc) -> None:
        if self._stopped:
            return
        ok = exc[0] is None
        self.stop(ok=ok)

    def _loop(self) -> None:
        while not self._stop.is_set():
            self._render()
            time.sleep(0.1)

    # ---- state
    def update(self, done: float | None = None, total: float | None = None,
               detail: str | None = None, text: str | None = None) -> None:
        with self._lock:
            if done is not None:
                self.done = done
                self._samples.append((time.time(), float(done)))
                if len(self._samples) > 30:
                    self._samples.pop(0)
            if total is not None:
                self.total = total
            if detail is not None:
                self.detail = detail
            if text is not None:
                self.text = text

    def finish(self, text: str | None = None, total: float | None = None) -> None:
        with self._lock:
            self.text = text or self.text
            if total is not None:
                self.total = total
            self.done = self.total or self.done

    def stop(self, ok: bool = True, note: str | None = None) -> None:
        if self._stopped:
            return
        self._stopped = True
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
        if ANIMATE:
            clear_line()
        mark = "ok" if ok else "no"
        elapsed = duration(time.time() - self._t0)
        suffix = note if note is not None else f"· {elapsed}"
        line(mark, self.text, suffix,
             mark_style="green" if ok else "red")
        with Live._active_lock:
            if Live._active is self:
                Live._active = None

    # ---- rendering
    def speed(self) -> float | None:
        if len(self._samples) < 2:
            return None
        (t0, d0), (t1, d1) = self._samples[0], self._samples[-1]
        if t1 - t0 < 0.35:
            return None
        rate = (d1 - d0) / (t1 - t0)
        return rate if rate > 0 else None

    def _bar(self, now: float) -> str:
        b = self.bar_w
        if self.total:
            frac = max(0.0, min(1.0, self.done / self.total)) if self.total else 0.0
            filled = int(round(frac * b))
            return "█" * filled + "░" * (b - filled)
        span = b + 9
        head = int((now - self._t0) * 16) % (span * 2)
        pos = head if head <= span else span * 2 - head
        start = max(0, min(b, pos - 9))
        end = max(0, min(b, pos))
        return "░" * start + "▓" * (end - start) + "░" * (b - end)

    def _suffix(self, now: float) -> str:
        if self.total:
            frac = max(0.0, min(1.0, self.done / self.total))
            parts = [f"{int(frac * 100):3d}%", f"{human(self.done)} / {human(self.total)}"]
            speed = self.speed()
            if speed:
                parts.append(f"{human(speed)}/s")
                remain = max(0.0, (self.total - self.done) / speed)
                parts.append(f"sisa {duration(remain)}")
            return "  ".join(parts)
        return ""

    def _render(self) -> None:
        with self._lock:
            if self._stopped:
                return
            now = time.time()
            bar = self._bar(now)
            suffix = self._suffix(now)
            detail = self.detail
            head = "  " + " " * self.indent + "▸ "
            room = WIDTH - vlen(head) - self.bar_w - 4 - len(suffix) - 2
            if detail:
                detail = detail if len(detail) <= room else detail[: max(0, room - 1)] + "…"
            text = f"{head}[{bar}]"
            if suffix:
                text += "  " + suffix
            if detail:
                text += "  " + c(detail, "gray")
            sys.stdout.write("\r\033[K" + text)
            sys.stdout.flush()


# --------------------------------------------------------------------- prompts

def ask(prompt: str, default: str = "") -> str:
    suffix = f" ({default})" if default else ""
    try:
        return input(c(f"  {prompt}{suffix} › ", "bold")).strip()
    except (EOFError, KeyboardInterrupt):
        w()
        raise SystemExit(130) from None


def confirm(prompt: str, default: bool = True) -> bool:
    hint = "[Y/n]" if default else "[y/N]"
    while True:
        try:
            raw = input(f"  {c(prompt, 'bold')} {c(hint, 'gray')} › ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            w()
            raise SystemExit(130) from None
        if not raw:
            return default
        if raw in ("y", "ya", "yes"):
            return True
        if raw in ("n", "no", "tidak", "t"):
            return False
        w("  " + c("Mohon jawab y atau n.", "yellow"))


def countdown(seconds: float, text: str, cancel_hint: str = "") -> None:
    """Hitung mundur singkat sambil menampilkan pesan."""
    if not ANIMATE:
        w("  " + text)
        return
    end = time.time() + seconds
    while time.time() < end:
        remain = end - time.time()
        sys.stdout.write("\r\033[K  " + text + c(f"  ({remain:.1f})", "gray")
                         + (("  " + c(cancel_hint, "gray")) if cancel_hint else ""))
        sys.stdout.flush()
        time.sleep(0.1)
    clear_line()
    w("  " + text)
