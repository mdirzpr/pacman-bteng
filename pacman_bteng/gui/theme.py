"""Colour palettes for the whole GUI — two presets, one frozen object each.

Every pixel the renderer, the HUD and the effect layer draw takes its colour
from a :class:`Theme` instance that is passed in.  Keeping the palette in a
single immutable value (instead of module-level constants) means a test or a
tool can render the same world with a different look without monkey-patching
anything — and it is what lets the game ship two complete looks:

``VisualStyle.LITE``
    The default, and the calm / reduced-motion option.  A modern arcade
    re-release: a near-black base that is deliberately *not* ``#000`` (pure
    black flattens the sense of depth and makes every edge look painted on),
    cobalt walls with a genuinely luminous edge, warm-white pellets, and a
    side panel that reads like a good dark-mode dashboard rather than like
    leftover screen.  ``glow_strength`` is 0 and ``scanlines`` is off, so a
    renderer can take its cheap path without asking which style it has.

``VisualStyle.DELUXE``
    Retro-futurism / synthwave.  Base near ``#020617``, neon cyan wall edges,
    magenta accents, violet borders — saturated, but every text pair still
    clears WCAG AA.  ``glow_strength`` ~1 and ``scanlines`` on.

Two rules hold across *both* palettes:

* The four ghost colours stay recognisably the canonical red / pink / cyan /
  orange.  They are the game's identity, and the panel colour-codes its blocks
  off them, so they may be tuned for legibility but never re-hued.
* Nothing meaningful is carried by red-vs-green alone.  The ghosts separate on
  luminance and on the blue channel (which every common colour-vision
  deficiency keeps), and the node-status colours are spread across lightness
  as well as hue for the same reason.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Any, Dict, Mapping, Tuple

#: An RGB triple.  Alpha is never stored here — the few translucent surfaces the
#: renderer builds add their own alpha at blit time.
Colour = Tuple[int, int, int]


class VisualStyle(Enum):
    """Which of the two complete looks a :class:`Theme` is.

    The value is the lower-case string used in ``config/game.yaml`` and on the
    command line, so ``VisualStyle(text)`` parses a user-supplied name.
    """

    LITE = "lite"
    DELUXE = "deluxe"


# ── The canonical ghost colours ───────────────────────────────────────────────
# Keyed by the agent names used everywhere else (``GHOST_NAMES`` in config.py),
# so the HUD can colour a panel block straight from ``brain.name``.
#
# LITE lifts pure red to (255, 45, 45): pure red has a relative luminance of
# only 0.21, which on a near-black background is the one ghost that reads as a
# dark shape rather than a bright one.  The four luminances then land at
# roughly 0.23 / 0.63 / 0.79 / 0.56 — far enough apart that a protanope or
# deuteranope, who loses the red-green hue axis, still tells them apart by
# brightness plus the blue channel (pinky and inky have it, clyde has none).
_GHOSTS_LITE: Mapping[str, Colour] = MappingProxyType(
    {
        "blinky": (255, 45, 45),      # red    — the chaser
        "pinky": (255, 184, 255),     # pink   — the ambusher
        "inky": (0, 255, 255),        # cyan   — the flanker
        "clyde": (255, 176, 74),      # orange — the shy one
    }
)

# DELUXE keeps the same four hues and the same brightness ordering; it only
# pushes them toward the neon end so they sit inside the synthwave palette.
_GHOSTS_DELUXE: Mapping[str, Colour] = MappingProxyType(
    {
        "blinky": (255, 49, 71),
        "pinky": (255, 168, 246),
        "inky": (34, 240, 255),
        "clyde": (255, 168, 66),
    }
)

# Keyed by ``bteng.NodeStatus.value``, which is the upper-case name ("RUNNING",
# "SUCCESS", "FAILURE", "IDLE").  The HUD looks the key up leniently so an
# unknown status degrades to dim text instead of raising.
#
# SUCCESS and FAILURE are the one pair a reader must never confuse, and green
# vs red is exactly the pair red-green colour blindness destroys.  So they are
# separated on lightness too: SUCCESS is a light mint, FAILURE a mid rose, and
# RUNNING an amber that is brighter than both.
_STATUS_LITE: Mapping[str, Colour] = MappingProxyType(
    {
        "RUNNING": (250, 204, 21),
        "SUCCESS": (110, 231, 183),
        "FAILURE": (248, 113, 113),
        "IDLE": (148, 158, 180),
    }
)

_STATUS_DELUXE: Mapping[str, Colour] = MappingProxyType(
    {
        "RUNNING": (255, 214, 64),
        "SUCCESS": (57, 255, 190),
        "FAILURE": (255, 92, 138),
        "IDLE": (140, 146, 190),
    }
)


@dataclass(frozen=True)
class Theme:
    """Every colour the GUI needs, in one place.

    The field defaults *are* the LITE palette, so a bare ``Theme()`` is a
    complete, usable look; :meth:`for_style` overrides them for DELUXE.
    """

    style: VisualStyle = VisualStyle.LITE

    # ── Maze ──────────────────────────────────────────────────────────────────
    background: Colour = (10, 12, 18)
    wall: Colour = (30, 58, 138)
    wall_edge: Colour = (96, 165, 250)
    wall_glow: Colour = (37, 99, 235)
    door: Colour = (249, 168, 212)
    grid_line: Colour = (26, 32, 48)

    # ── Pickups and agents ────────────────────────────────────────────────────
    pellet: Colour = (255, 233, 196)
    power_pellet: Colour = (255, 246, 224)
    pacman: Colour = (255, 214, 10)
    pacman_dim: Colour = (150, 126, 18)
    frightened: Colour = (41, 55, 166)
    frightened_blink: Colour = (233, 238, 255)
    ghost_eye: Colour = (255, 255, 255)
    ghost_pupil: Colour = (18, 24, 64)

    # ── Chrome and type ───────────────────────────────────────────────────────
    text: Colour = (230, 236, 246)
    text_bright: Colour = (255, 255, 255)
    text_dim: Colour = (150, 160, 182)
    panel_bg: Colour = (14, 17, 26)
    panel_card: Colour = (24, 29, 43)
    panel_border: Colour = (48, 57, 79)
    accent: Colour = (56, 189, 248)
    accent_dim: Colour = (24, 96, 130)
    danger: Colour = (248, 113, 113)
    success: Colour = (110, 231, 183)
    warning: Colour = (250, 204, 21)
    overlay_scrim: Colour = (6, 8, 13)

    # ── Lookup tables and effect switches ─────────────────────────────────────
    ghosts: Mapping[str, Colour] = field(default_factory=lambda: _GHOSTS_LITE)
    status: Mapping[str, Colour] = field(default_factory=lambda: _STATUS_LITE)

    #: How hard the effect layer may push bloom/halo work, 0..1.  LITE is 0 so
    #: an effect layer can branch on the number instead of on the enum.
    glow_strength: float = 0.0
    #: Whether a CRT scanline overlay belongs over the maze.
    scanlines: bool = False

    # ── Construction ──────────────────────────────────────────────────────────

    @classmethod
    def default(cls) -> "Theme":
        """The default palette (LITE).

        Exists so callers never construct a Theme by hand and accidentally pick
        up a half-filled one.
        """
        return cls.for_style(VisualStyle.LITE)

    @classmethod
    def for_style(cls, style: VisualStyle) -> "Theme":
        """The complete palette for *style*.

        Anything not listed in the override table below keeps its LITE value,
        which is why a new field only has to be added in one place to exist in
        both presets — it just starts out shared until someone tunes it.
        """
        if style is VisualStyle.LITE:
            return cls()
        overrides = _DELUXE_OVERRIDES if style is VisualStyle.DELUXE else {}
        return cls(style=style, **overrides)

    # ── Lookups ───────────────────────────────────────────────────────────────

    def agent_colour(self, name: str) -> Colour:
        """Colour for *name*: a ghost colour, or Pac-Man yellow for anyone else.

        Pac-Man shows up in the panel too when autopilot is on, and he is not in
        :attr:`ghosts`, so this fallback is load-bearing rather than defensive.
        """
        return self.ghosts.get(name.lower(), self.pacman)

    def status_colour(self, status_name: str) -> Colour:
        """Colour for a ``NodeStatus`` value; dim text for anything unknown."""
        return self.status.get(str(status_name).upper(), self.text_dim)


# ── The DELUXE palette ────────────────────────────────────────────────────────
# Kept as an override table rather than a second dataclass so the two presets
# can never drift apart in *shape*: every key here is a field above, and any
# field missing from here is intentionally shared with LITE.
_DELUXE_OVERRIDES: Dict[str, Any] = {
    # Maze: indigo walls rimmed in neon cyan, over the synthwave near-black.
    "background": (2, 6, 23),
    "wall": (40, 22, 92),
    "wall_edge": (0, 229, 255),
    "wall_glow": (0, 132, 176),
    "door": (255, 121, 198),
    "grid_line": (32, 24, 72),
    # Pickups: warmer pacman, pink-tinted power pellets to match the door.
    "pellet": (255, 240, 214),
    "power_pellet": (255, 226, 245),
    "pacman": (255, 225, 53),
    "pacman_dim": (156, 132, 24),
    "frightened": (67, 56, 202),
    "frightened_blink": (240, 245, 255),
    "ghost_pupil": (18, 8, 60),
    # Chrome: violet borders, magenta accent — the accent is what the panel
    # uses for headings and the active node, so it is checked for contrast.
    "text": (232, 236, 252),
    "text_dim": (154, 158, 200),
    "panel_bg": (8, 11, 32),
    "panel_card": (23, 24, 58),
    "panel_border": (69, 43, 130),
    "accent": (255, 79, 216),
    "accent_dim": (128, 40, 110),
    "danger": (255, 84, 122),
    "success": (57, 255, 190),
    "warning": (255, 200, 60),
    "overlay_scrim": (3, 4, 18),
    "ghosts": _GHOSTS_DELUXE,
    "status": _STATUS_DELUXE,
    "glow_strength": 1.0,
    "scanlines": True,
}


# ── Colour maths ──────────────────────────────────────────────────────────────

def blend(a: Colour, b: Colour, t: float) -> Colour:
    """Mix *a* toward *b* by *t* in ``[0, 1]``.

    Used instead of per-surface alpha for the debug overlay: blending against
    the known background is one tuple of arithmetic per frame, where an alpha
    surface would be an allocation plus a blit.
    """
    t = 0.0 if t < 0.0 else 1.0 if t > 1.0 else t
    return (
        int(a[0] + (b[0] - a[0]) * t),
        int(a[1] + (b[1] - a[1]) * t),
        int(a[2] + (b[2] - a[2]) * t),
    )
