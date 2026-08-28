"""Layout — the single place where tile units become pixels.

The game layer thinks entirely in tiles (:mod:`pacman_bteng.game.geometry`);
pygame thinks in pixels.  Every conversion between the two happens here, so the
renderer, the HUD and the debug overlay can never disagree about where tile
``(13, 23)`` actually is on screen.

The window is four stacked/side-by-side regions::

    ┌───────────────────────────────┬──────────┐
    │ top bar   SCORE HIGH LEVEL    │          │
    ├───────────────────────────────┤  panel   │
    │                               │  (live   │
    │            maze               │   BT     │
    │                               │  state)  │
    ├───────────────────────────────┤          │
    │ bottom bar  lives + key hint  │          │
    └───────────────────────────────┴──────────┘

The panel spans the full window height on purpose: it is the point of the
project, and a tall column fits four ghost blocks without scrolling.

Spacing and type
----------------

Every gap in the HUD is a multiple of one number, :attr:`Layout.pad`, and every
string is drawn at one of five sizes from :meth:`Layout.font_size`.  Both derive
from the tile size, so shrinking the window to fit a laptop screen scales the
chrome with the maze instead of leaving 24 px text beside 10 px tiles.

The type scale is a major third (ratio 1.25) around a base that tracks the tile
and is clamped to a legible band::

    base   = clamp(round(tile * 0.68), 13, 22)      # 16 px at the default tile

    role      step   size at tile=24   used for
    small      -1          13          labels in the bars, key hints, footnotes
    label       0          16          panel field names, secondary text
    value       1          20          scores, agent names - the things you read
    title       2          25          panel section headings
    overlay     5          49          READY! / GAME OVER across the maze

Five steps to the overlay looks like a jump, and it is: it is display type, not
body type, and it has a whole maze to fill.  The bar heights are then derived
from the same numbers (``value`` plus two pads for the top bar, ``label`` plus
two pads for the bottom one) rather than from a raw multiple of the tile, which
is what stops the text being clipped once the tile shrinks past the font floor.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

from pacman_bteng.game.maze import Maze

#: Below this the maze stops being readable, so :meth:`Layout.compute` gives up
#: shrinking rather than producing an unusable window.
MIN_TILE = 6

#: The panel needs a floor in *pixels*, not in tiles: its content is text at a
#: readable font size, which does not shrink with the maze.
MIN_PANEL_W = 300

#: A major third.  Big enough that two neighbouring roles are obviously not the
#: same size, small enough that five steps still fit in one window.
TYPE_RATIO = 1.25

#: Where each role sits on the scale, in steps of :data:`TYPE_RATIO` from the
#: base size.  ``font_size()`` falls back to ``"label"`` (step 0) for anything
#: not listed, so an unknown role degrades to body text instead of raising.
TYPE_STEPS = {
    "small": -1,
    "label": 0,
    "value": 1,
    "title": 2,
    "overlay": 5,
}

#: The base size is clamped: below ~13 px the default pygame font stops being
#: readable, and above ~22 px the panel text starts to wrap.
MIN_BASE_FONT = 13
MAX_BASE_FONT = 22


def _base_font(tile: int) -> int:
    """Base ("label") type size for a given tile size, clamped to the band."""
    return max(MIN_BASE_FONT, min(MAX_BASE_FONT, int(round(tile * 0.68))))


def _pad(tile: int) -> int:
    """Base spacing unit for a given tile size.

    A free function as well as a property because :meth:`Layout._at` needs it
    to size the bars *before* there is a Layout to ask.
    """
    return max(4, int(round(tile * 0.34)))


@dataclass(frozen=True)
class Layout:
    """Pixel geometry for one window size.  Immutable; recompute to resize."""

    tile: int
    cols: int
    rows: int
    top_bar: int
    bottom_bar: int
    panel_w: int

    # ── Window regions ────────────────────────────────────────────────────────

    @property
    def maze_w(self) -> int:
        return self.cols * self.tile

    @property
    def maze_h(self) -> int:
        return self.rows * self.tile

    @property
    def width(self) -> int:
        return self.maze_w + self.panel_w

    @property
    def height(self) -> int:
        return self.top_bar + self.maze_h + self.bottom_bar

    @property
    def size(self) -> Tuple[int, int]:
        return (self.width, self.height)

    @property
    def maze_origin(self) -> Tuple[int, int]:
        """Top-left pixel of tile ``(0, 0)``."""
        return (0, self.top_bar)

    @property
    def panel_x(self) -> int:
        """Left edge of the right-hand panel (== window width when hidden)."""
        return self.maze_w

    @property
    def bottom_bar_y(self) -> int:
        return self.top_bar + self.maze_h

    # ── Spacing and type ──────────────────────────────────────────────────────

    @property
    def pad(self) -> int:
        """The base spacing unit; every HUD gap is a multiple of this.

        Derived from the tile so the chrome breathes the same amount whatever
        window size ``compute()`` settled on.
        """
        return _pad(self.tile)

    @property
    def gutter(self) -> int:
        """The wider gap: between panel cards, and around block edges.

        Two pads rather than a separate number, so the rhythm stays obviously
        related when you look at the window.
        """
        return self.pad * 2

    def font_size(self, role: str = "label") -> int:
        """Pixel size for a type *role* on the major-third scale.

        Roles are ``"title"``, ``"value"``, ``"label"``, ``"small"`` and
        ``"overlay"``; anything else is treated as ``"label"`` so a typo in a
        draw call is a wrong size, never a crash mid-frame.
        """
        base = _base_font(self.tile)
        step = TYPE_STEPS.get(str(role).lower(), 0)
        return max(8, int(round(base * (TYPE_RATIO ** step))))

    # ── Tile → pixel ──────────────────────────────────────────────────────────

    def tile_px(self, tile: Tuple[int, int]) -> Tuple[int, int]:
        """Top-left pixel of *tile* — the anchor for a full-tile rect."""
        ox, oy = self.maze_origin
        return (ox + tile[0] * self.tile, oy + tile[1] * self.tile)

    def centre_px(self, pos: Tuple[float, float]) -> Tuple[int, int]:
        """Pixel centre of a *continuous* tile position.

        ``Agent.position`` returns tile units as floats, so the ``+ 0.5`` is
        what puts an agent in the middle of its tile rather than on its corner.
        """
        ox, oy = self.maze_origin
        return (
            int(ox + (pos[0] + 0.5) * self.tile),
            int(oy + (pos[1] + 0.5) * self.tile),
        )

    def tile_rect(self, tile: Tuple[int, int]) -> Tuple[int, int, int, int]:
        """``(x, y, w, h)`` covering *tile* — handed straight to pygame.Rect."""
        x, y = self.tile_px(tile)
        return (x, y, self.tile, self.tile)

    # ── Construction ──────────────────────────────────────────────────────────

    @classmethod
    def compute(
        cls,
        maze: Maze,
        tile_size: int,
        show_panel: bool = True,
        max_size: Optional[Tuple[int, int]] = None,
    ) -> "Layout":
        """Layout for *maze*, shrunk until it fits inside *max_size*.

        The requested *tile_size* is a maximum, not a promise: a 28x31 maze at
        24 px plus the panel is nearly 1000x800, which does not fit on a laptop
        with a menu bar.  Shrinking one pixel at a time (rather than solving for
        the tile size) keeps the bar heights — which are derived from the tile —
        consistent with whatever size is finally chosen.
        """
        requested = max(int(tile_size), MIN_TILE)
        for tile in range(requested, MIN_TILE - 1, -1):
            layout = cls._at(maze, tile, show_panel)
            if max_size is None:
                return layout
            if layout.width <= max_size[0] and layout.height <= max_size[1]:
                return layout
        # Even the smallest readable tile overflows; return it anyway so the
        # caller gets a window rather than an exception.
        return cls._at(maze, MIN_TILE, show_panel)

    @classmethod
    def _at(cls, maze: Maze, tile: int, show_panel: bool) -> "Layout":
        """Layout at an exact tile size — bars and panel derived from it."""
        # The bars are sized from the type scale, not from a multiple of the
        # tile: the fonts stop shrinking at MIN_BASE_FONT, so a bar tied to the
        # tile alone would keep shrinking past its own text and clip it.
        pad = _pad(tile)
        return cls(
            tile=tile,
            cols=maze.width,
            rows=maze.height,
            top_bar=max(28, cls._role_px(tile, "value") + 2 * pad),
            bottom_bar=max(24, cls._role_px(tile, "label") + 2 * pad),
            panel_w=max(MIN_PANEL_W, tile * 12) if show_panel else 0,
        )

    @staticmethod
    def _role_px(tile: int, role: str) -> int:
        """``font_size()`` for a tile size, before a Layout exists to ask."""
        base = _base_font(tile)
        return max(8, int(round(base * (TYPE_RATIO ** TYPE_STEPS.get(role, 0)))))

    def __repr__(self) -> str:  # noqa: D105 - printed by --debug start-up logs
        return (
            f"Layout(tile={self.tile}, maze={self.cols}x{self.rows}, "
            f"window={self.width}x{self.height}, panel={self.panel_w})"
        )
