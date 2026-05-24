"""
Animated loading display — random nonsense words cycling through blue/green.
Used while waiting for the first streaming token and during tool execution.
"""

import random
import time

from rich.text import Text

# ── Nonsense word bank ────────────────────────────────────────────────────────

_WORDS = [
    "blorpling",    "snazzlewump",  "zibbleforth",  "flempering",
    "bazzledorf",   "snorflequist", "wibblefurst",  "discombobulating",
    "flibbertigibbet", "zymurgical","quizzaciously","snazzlefrump",
    "grobblesnatch","fizzbonk",     "clabbersnoot", "drizzlefump",
    "snaggleplex",  "borblewick",   "quirklefrump", "zippledorf",
    "bafflesnorp",  "grizbleforth", "wumblecrunk",  "snorfleplonk",
    "zippledoodle", "bazzlewump",   "florblewick",  "quibblewurst",
    "drimblewock",  "flabbersnatch","zorblewump",   "sniggleforth",
    "bumblewuzzel", "crumblesnoot", "flippertwist", "zorblefrump",
    "frumplezorp",  "blibblewick",  "snorplefuzz",  "zibblequirk",
    "flumperdoodle","gribblesnort", "snazzlenork",  "borplewump",
    "frizzlesnatch","zorblewiggle", "sniggleplonk", "bumblefrizz",
    "grumblezorp",  "flibberwump",  "snorklefrizz", "zimblewack",
    "grobblefrump", "snazzlequirk", "wumplesnort",  "drimblewuzzel",
    "flibberplonk", "zorplewiggle", "snagglewump",  "quirklefrump",
    "blorplesnatch","zibbleplonk",  "snorflefrizz", "wibblefrump",
    "crumblezorp",  "flippersnoot", "borplewiggle", "grizzlesnatch",
]

# ── Spinner frames ────────────────────────────────────────────────────────────

_SPIN = "⣾⣷⣯⣟⡿⢿⣻⣽"

# ── Blue-green color wave ─────────────────────────────────────────────────────
# Each slot in the wave has a (primary, accent) pair
_WAVE = [
    ("blue",         "bright_blue"),
    ("bright_blue",  "cyan"),
    ("cyan",         "bright_cyan"),
    ("bright_cyan",  "bright_green"),
    ("bright_green", "green"),
    ("green",        "cyan"),
    ("cyan",         "bright_blue"),
    ("bright_blue",  "blue"),
]

# ── Short loading verbs (so the label reads like a sentence) ─────────────────

_VERBS = [
    "blorpling",     "snazzling",   "zibblefrothing", "wumbling",
    "discombobulating", "quirkifying", "grumblezorping", "snorflequesting",
    "flempering",    "borplewiggling", "frizzlesnatching", "zorblewumping",
    "snaggleplex·ing", "flippertwisting", "bazzledorfing",
]

# ── Public API ────────────────────────────────────────────────────────────────

class LoadingAnimation:
    """
    Rich renderable that auto-animates on each Live refresh.

    Shows a braille spinner, a random loading verb, and 3 cycling nonsense
    words that wave through blue and green shades.
    """

    def __init__(self, label: str = "thinking"):
        self._label = label
        self._t0 = time.monotonic()
        # Snapshot a shuffled word list so the same word doesn't repeat too soon
        self._pool = random.sample(_WORDS, len(_WORDS))
        self._verb_pool = random.sample(_VERBS, len(_VERBS))

    def __rich__(self) -> Text:
        now = time.monotonic() - self._t0

        spin = _SPIN[int(now * 10) % len(_SPIN)]

        # Verb changes every ~1.8 s
        verb = self._verb_pool[int(now / 1.8) % len(self._verb_pool)]

        # Three words, each at a different speed so they drift apart
        w0 = self._pool[int(now * 1.1) % len(self._pool)]
        w1 = self._pool[int(now * 0.9 + 17) % len(self._pool)]
        w2 = self._pool[int(now * 1.3 + 31) % len(self._pool)]

        # Color wave offset advances at ~2 steps/s
        wave_off = int(now * 2)

        t = Text(overflow="fold")
        t.append(f"{spin} ", style="bold bright_blue")
        t.append(f"{verb}... ", style="dim")

        for i, word in enumerate((w0, w1, w2)):
            pri, acc = _WAVE[(wave_off + i * 2) % len(_WAVE)]
            # Pulse: alternate between primary and accent at 3 Hz
            style = pri if int(now * 3 + i) % 2 == 0 else acc
            t.append(word, style=f"bold {style}" if i == 0 else style)
            if i < 2:
                t.append("  ·  ", style="dim")

        return t


def tool_phrase(tool_name: str) -> Text:
    """
    Return a one-liner animated phrase for a tool execution status line.
    Picks a random nonsense verb and adds the tool name dimly.
    """
    verb = random.choice(_VERBS)
    noun = random.choice(_WORDS)
    pri, acc = random.choice(_WAVE)
    t = Text(overflow="fold")
    t.append("⟳ ", style="bold bright_blue")
    t.append(verb, style=f"bold {pri}")
    t.append(" the ", style="dim")
    t.append(noun, style=acc)
    t.append(f"  [dim]({tool_name})[/dim]", style="")
    return t
