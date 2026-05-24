"""
Arrow-key interactive selector for the ask_user tool.
Uses raw terminal I/O — no external dependencies.
Falls back to numbered input when stdin/stdout are not a tty.
"""

import os
import sys
import select as _select_mod
import termios
import tty
from typing import Optional

# ── ANSI codes ────────────────────────────────────────────────────────────────

_HIDE  = "\x1b[?25l"
_SHOW  = "\x1b[?25h"
_CLEAR = "\x1b[2K\r"
_UP    = "\x1b[1A"
_RST   = "\x1b[0m"
_BOLD  = "\x1b[1m"
_DIM   = "\x1b[2m"
_CYAN  = "\x1b[96m"
_GREEN = "\x1b[92m"
_YLW   = "\x1b[93m"

# ── Key reading ───────────────────────────────────────────────────────────────

def _read_key(fd: int) -> str:
    ch = os.read(fd, 1).decode("utf-8", errors="replace")
    if ch == "\x1b":
        # Read rest of escape sequence with a short timeout
        ready, _, _ = _select_mod.select([fd], [], [], 0.05)
        if ready:
            rest = os.read(fd, 8).decode("utf-8", errors="replace")
            return "\x1b" + rest
        return "\x1b"
    return ch


def _is_up(k: str) -> bool:
    return k in ("\x1b[A", "\x1bOA", "k", "\x1b[1;2A")


def _is_down(k: str) -> bool:
    return k in ("\x1b[B", "\x1bOB", "j", "\x1b[1;2B")


def _is_enter(k: str) -> bool:
    return k in ("\r", "\n")


def _is_space(k: str) -> bool:
    return k == " "

# ── Rendering ─────────────────────────────────────────────────────────────────

def _render(question: str, options: list[str], cursor: int,
            checked: set[int], multi: bool) -> list[str]:
    lines: list[str] = []

    hint = "Space to toggle, Enter to confirm" if multi else "Enter to confirm"
    lines.append(f"  {_BOLD}?{_RST} {question}")
    lines.append(f"  {_DIM}↑/↓ move  {hint}  Esc/Ctrl+C cancel{_RST}")
    lines.append("")

    for i, opt in enumerate(options):
        at_cursor = i == cursor
        is_checked = i in checked

        if multi:
            cb = f"{_CYAN}◉{_RST}" if is_checked else f"{_DIM}◯{_RST}"
            arrow = f"{_CYAN}❯{_RST}" if at_cursor else " "
            label = f"{_CYAN}{_BOLD}{opt}{_RST}" if at_cursor else opt
            lines.append(f"  {arrow} {cb} {label}")
        else:
            arrow = f"{_CYAN}❯{_RST}" if at_cursor else " "
            label = f"{_CYAN}{_BOLD}{opt}{_RST}" if at_cursor else opt
            lines.append(f"  {arrow} {label}")

    return lines


def _write(s: str):
    sys.stdout.write(s)
    sys.stdout.flush()


def _erase(n: int):
    for _ in range(n):
        _write(_UP + _CLEAR)

# ── Fallback (non-tty) ────────────────────────────────────────────────────────

def _fallback(question: str, options: list[str], multi: bool,
              allow_freetext: bool) -> dict:
    all_opts = list(options) + (["[type custom answer]"] if allow_freetext else [])
    print(f"\n? {question}")
    for i, opt in enumerate(all_opts, 1):
        print(f"  {i}. {opt}")

    while True:
        try:
            raw = input("  " + ("Numbers separated by comma: " if multi else "Number: ")).strip()
            if multi:
                idxs = [int(x.strip()) - 1 for x in raw.split(",") if x.strip()]
                sel = [all_opts[i] for i in idxs if 0 <= i < len(all_opts)]
                if sel:
                    return {"ok": True, "selection": sel, "cancelled": False}
            else:
                idx = int(raw) - 1
                if 0 <= idx < len(all_opts):
                    chosen = all_opts[idx]
                    if allow_freetext and idx == len(all_opts) - 1:
                        chosen = input("  Your answer: ").strip()
                    return {"ok": True, "selection": chosen, "cancelled": False}
            print("  Invalid — try again.")
        except (ValueError, IndexError):
            print("  Invalid — try again.")
        except (EOFError, KeyboardInterrupt):
            return {"ok": True, "selection": None, "cancelled": True}

# ── Main entry ────────────────────────────────────────────────────────────────

def ask(question: str, options: list[str],
        multi: bool = False,
        allow_freetext: bool = False) -> dict:
    """
    Show an arrow-key selector and return the result dict:
      {"ok": True, "selection": str | list[str] | None, "cancelled": bool}
    """
    if allow_freetext:
        options = list(options) + ["[type a custom answer]"]

    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return _fallback(question, options, multi, allow_freetext=False)

    cursor = 0
    checked: set[int] = set()
    fd = sys.stdin.fileno()
    saved = termios.tcgetattr(fd)
    rendered: list[str] = []

    def draw():
        nonlocal rendered
        rendered = _render(question, options, cursor, checked, multi)
        _write("\n" + "\n".join(rendered) + "\n")

    def redraw():
        # erase: rendered lines + trailing newline
        _erase(len(rendered) + 1)
        draw()

    draw()

    result = {"ok": True, "selection": None, "cancelled": True}

    try:
        tty.setraw(fd)
        while True:
            key = _read_key(fd)

            if key in ("\x03", "\x1b"):   # Ctrl+C or bare Escape → cancel
                break

            elif _is_up(key):
                cursor = (cursor - 1) % len(options)
                redraw()

            elif _is_down(key):
                cursor = (cursor + 1) % len(options)
                redraw()

            elif _is_space(key) and multi:
                if cursor in checked:
                    checked.discard(cursor)
                else:
                    checked.add(cursor)
                redraw()

            elif _is_enter(key):
                # Restore terminal BEFORE any further I/O
                termios.tcsetattr(fd, termios.TCSADRAIN, saved)
                _write(_SHOW)

                if multi:
                    sel_indices = sorted(checked) if checked else [cursor]
                    chosen: str | list[str] = [options[i] for i in sel_indices]
                else:
                    chosen = options[cursor]
                    if allow_freetext and cursor == len(options) - 1:
                        # erase the selector, ask for free text
                        _erase(len(rendered) + 1)
                        chosen = input(f"  {_BOLD}Your answer:{_RST} ").strip()

                result = {"ok": True, "selection": chosen, "cancelled": False}
                return result

    except Exception:
        pass
    finally:
        try:
            termios.tcsetattr(fd, termios.TCSADRAIN, saved)
        except Exception:
            pass
        _write(_SHOW)

    # cancelled — erase selector
    _erase(len(rendered) + 1)
    return result
