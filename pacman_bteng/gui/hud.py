"""HUD — the arcade chrome plus the live behavior-tree panel.

The top and bottom bars are the usual arcade furniture (score, level, lives).
The right-hand panel is the reason this project exists: it shows, per agent and
per frame, which branch of its XML tree is running, which leaf node is active,
what the last tick returned and what tile the tree is steering toward.  Reading
that column next to the maze is how you *see* a behavior tree think, without
attaching a viewer over ZMQ.

Three ideas drive the visual design here:

* **Hierarchy, not columns.**  The old bar gave SCORE, HIGH, LEVEL and MODE the
  same weight in four equal cells, so nothing read first.  SCORE is now the
  hero number on a shared baseline with quieter secondaries, and MODE is a
  colour-coded pill that doubles as the power-pellet meter — a *status*, which
  is what it actually is.
* **Records, not rows.**  Each agent gets a card: an identity strip in its own
  ghost colour, the name, its game state, the ``branch › node`` path, a status
  chip and the target tile, all on fixed columns.  Five cards then scan as a
  column of records while the game is moving, which loose label/value text
  never did.
* **Nothing costs a Surface.**  Every string goes through :class:`TextCache`,
  so a frame is dict lookups and blits; see the class docstring for why that
  matters more here than anywhere else in the renderer.

The panel takes ``brains`` as a plain sequence and only ever touches
``.name`` / ``.status`` / ``.branch`` / ``.active_node`` / ``.describe()``, so
this module never imports :mod:`pacman_bteng.behaviors` — the GUI stays usable
(and testable) with no behavior-tree layer attached at all.

Both visual presets share this file.  ``theme.style`` / ``theme.glow_strength``
only ever add an extra hairline or halo *inside* geometry that was already
computed; every rectangle, baseline and card height is identical in LITE and
DELUXE, so pressing F3 never reflows the HUD.
"""

from __future__ import annotations

import math
import time
from collections import deque
from typing import Any, Dict, List, Optional, Sequence, Tuple

import pygame

from pacman_bteng.game.entities import Ghost
from pacman_bteng.game.geometry import manhattan
from pacman_bteng.game.world import GamePhase, GameWorld
from pacman_bteng.gui.input import KEY_HINT
from pacman_bteng.gui.layout import Layout
from pacman_bteng.gui.theme import Colour, Theme, blend

#: Manhattan distance at which the per-card proximity strip reads as empty.
#: The maze is 28x31, so a raw normalisation would keep every strip near zero;
#: 24 tiles is roughly "two corridors away", which is where a chase starts to
#: matter.
PROXIMITY_RANGE = 24.0

#: Score sparkline: one sample per second, the last ``SPARK_SAMPLES`` of them.
SPARK_INTERVAL = 1.0
SPARK_SAMPLES = 30
#: Narrowest sparkline worth drawing; below this the bar drops it instead.
SPARK_MIN_W = 48
SPARK_MAX_W = 120

#: Proximity at which a hostile ghost's card starts to lift (24 tiles => ~10
#: tiles away) and the proximity at which it is fully lifted (~4 tiles).
LIFT_FROM = 0.58
LIFT_TO = 0.83
#: DELUXE eases the lift in and out at this rate (fraction of the way per
#: second); LITE, the calm preset, moves the card in a single step.
LIFT_RATE = 9.0

#: Seconds a tab change cross-fades for.  Wall-clock, not game time: the fade
#: must finish while the game is paused (which is when you switch tabs).
TAB_FADE = 0.15

#: The side panel's tabs, in dock order: (key, label).
PANEL_TABS = (("trees", "TREES"), ("stats", "STATS"), ("log", "LOG"))
#: Most events the LOG tab remembers; it shows as many as fit.
LOG_KEEP = 40
#: Event kinds too frequent to read as a log (one per pellet).
LOG_SKIP = frozenset({"pellet"})


class FontBook:
    """Lazily built, cached ``pygame.font.Font`` objects.

    Always the default font (``Font(None, size)``): the contract says the GUI
    must never crash because a named system font is missing, and the built-in
    one ships inside pygame.
    """

    def __init__(self) -> None:
        self._cache: Dict[int, pygame.font.Font] = {}

    def at(self, size: int) -> pygame.font.Font:
        size = max(8, int(size))
        font = self._cache.get(size)
        if font is None:
            if not pygame.font.get_init():
                pygame.font.init()
            font = pygame.font.Font(None, size)
            self._cache[size] = font
        return font


class TextCache:
    """Rendered text surfaces, keyed by ``(font, text, colour)``.

    ``Font.render`` allocates and rasterises a Surface on every call, and the
    HUD redraws roughly forty strings per frame of which only the score and the
    clock ever change.  Caching turns almost all of that into a dict lookup and
    a blit, which is what keeps the panel inside the render budget.

    The cache is cleared wholesale once it passes :attr:`LIMIT` rather than
    evicted entry by entry: ``elapsed`` mints a new string ten times a second,
    so the dict *would* grow without bound, but it grows slowly enough that one
    clear every few thousand frames is cheaper than tracking recency.
    """

    LIMIT = 512

    def __init__(self) -> None:
        self._cache: Dict[Tuple[int, str, Colour], pygame.Surface] = {}

    def get(
        self, font: pygame.font.Font, text: str, colour: Colour
    ) -> pygame.Surface:
        # ``id(font)`` is a safe key because FontBook keeps every font it ever
        # built alive for the lifetime of the Hud, so an id cannot be reused.
        key = (id(font), text, colour)
        surface = self._cache.get(key)
        if surface is None:
            if len(self._cache) >= self.LIMIT:
                self._cache.clear()
            surface = font.render(text, True, colour)
            self._cache[key] = surface
        return surface

    # ── Placement helpers ─────────────────────────────────────────────────────

    def blit(
        self,
        target: pygame.Surface,
        font: pygame.font.Font,
        text: str,
        colour: Colour,
        pos: Tuple[int, int],
    ) -> int:
        """Draw *text* with its top-left at *pos*; returns the width used."""
        surface = self.get(font, text, colour)
        target.blit(surface, pos)
        return surface.get_width()

    def baseline(
        self,
        target: pygame.Surface,
        font: pygame.font.Font,
        text: str,
        colour: Colour,
        x: int,
        baseline: int,
    ) -> int:
        """Draw *text* sitting on *baseline*; returns the x just past it.

        Mixed type sizes on one line only look deliberate when they share a
        baseline — centring each fragment vertically (what the old bar did)
        makes the small label float in the middle of the big number.
        """
        surface = self.get(font, text, colour)
        target.blit(surface, (x, baseline - font.get_ascent()))
        return x + surface.get_width()

    def right(
        self,
        target: pygame.Surface,
        font: pygame.font.Font,
        text: str,
        colour: Colour,
        right: int,
        top: int,
    ) -> int:
        """Draw *text* right-aligned to *right*; returns its left edge."""
        surface = self.get(font, text, colour)
        x = right - surface.get_width()
        target.blit(surface, (x, top))
        return x


class Hud:
    """Draws the two bars and the behavior-tree panel onto a surface."""

    def __init__(self, layout: Layout, theme: Theme) -> None:
        self.layout = layout
        self.theme = theme
        self.fonts = FontBook()
        self.text = TextCache()
        self._glass_cache: Dict[Tuple[int, int, Colour], pygame.Surface] = {}
        # Score history for the sparkline.  Draw-layer state only: the game and
        # behavior layers know nothing about it, and a restart clears it.
        self._score_marks: List[int] = []
        self._score_clock = 0.0
        # LOG tab history, and the dock's hit boxes (valid after a draw).
        self._log: "deque[Tuple[float, str, str]]" = deque(maxlen=LOG_KEEP)
        self._log_clock = 0.0
        self._tab_rects: Dict[str, pygame.Rect] = {}
        # Per-card lift, 0..1, and the clock it is eased against.
        self._shown_tab: Optional[str] = None
        self._fade_from: Optional[pygame.Surface] = None
        self._fade_start = 0.0
        self._lift: Dict[str, float] = {}
        self._lift_clock = 0.0
        self._lift_dt = 0.0

        # One spacing unit for the whole HUD.  Layout owns it when it offers
        # it; the fallback keeps this module working against an older Layout
        # (and keeps the unit proportional to the tile, so a shrunken window
        # tightens up instead of keeping desktop-sized gaps).
        self.pad = int(getattr(layout, "pad", 0)) or max(6, layout.tile // 3)
        self.gap = max(3, self.pad // 2)

        # Font sizes track the tile size so a shrunken window stays legible
        # rather than keeping 24 px text next to 10 px tiles.  The bar fonts
        # are additionally capped against the bar height they must fit inside;
        # the panel fonts have a hard floor, as the panel width does
        # (MIN_PANEL_W).
        tile = layout.tile
        bar_h = layout.top_bar
        self.bar_hero_font = self.fonts.at(min(max(18, int(tile * 1.15)), int(bar_h)))
        self.bar_font = self.fonts.at(max(14, int(tile * 0.78)))
        self.bar_label_font = self.fonts.at(max(11, int(bar_h * 0.36)))
        self.small_font = self.fonts.at(max(13, int(tile * 0.62)))

        self.panel_title_font = self.fonts.at(self._role("title", 20))
        self.panel_name_font = self.fonts.at(self._role("value", 22))
        self.panel_font = self.fonts.at(self._role("label", 18))
        self.panel_small_font = self.fonts.at(self._role("small", 15))

        # Palette fields added by the visual refresh.  Resolved once, with a
        # fallback each, so the HUD still renders against a Theme that predates
        # them instead of raising mid-frame.
        self.card_bg = _colour(theme, "panel_card", theme.panel_bg)
        self.text_bright = _colour(theme, "text_bright", theme.text)
        self.accent_dim = _colour(theme, "accent_dim", theme.panel_border)
        self.success = _colour(theme, "success", theme.accent)
        self.warning = _colour(theme, "warning", theme.pacman)

        self.deluxe = float(getattr(theme, "glow_strength", 0.0)) > 0.0

        # The empty part of a card's gauge.  DELUXE's border violet is bright
        # enough to out-shout the text it sits under, so it gets pulled back
        # toward the card; LITE's is barely above the card and needs all of it.
        self.track = blend(self.card_bg, theme.panel_border, 0.6 if self.deluxe else 1.0)

        # A card is a fixed-height record: rows are three text lines plus the
        # proximity strip.  Precomputed because every card is the same height
        # and the panel needs the number before it starts laying cards out.
        self.row_h = self.panel_font.get_height()
        self.name_h = max(self.panel_name_font.get_height(), _ICON)
        self.card_h = (
            self.pad                      # top padding
            + self.name_h + self.gap      # row 1: identity
            + self.row_h + self.gap       # row 2: branch > node
            + self.row_h + self.gap       # row 3: status + target
            + _STRIP_H
            + self.pad                    # bottom padding
        )

    def _glass(self, width: int, height: int, tint: Colour) -> pygame.Surface:
        """A frosted card face: vertical sheen, a wash of *tint*, a top highlight.

        The "glassy dashboard" look reduced to what a flat pygame panel can
        afford: the card is lighter at the top than at the bottom, picks up a
        little of its agent's colour from the left, and has a 1 px bright top
        edge.  Built once per (size, tint) and cached, so a frame is a blit.
        """
        key = (width, height, tint)
        face = self._glass_cache.get(key)
        if face is not None:
            return face
        base = self.card_bg
        top = blend(base, (255, 255, 255), 0.09)
        face = pygame.Surface((width, height), pygame.SRCALPHA)
        for row in range(height):
            pygame.draw.line(face, blend(top, base, row / max(1, height - 1)),
                             (0, row), (width, row))
        # draw.line overwrites on an alpha surface, so the wash is painted on
        # its own layer and blitted, which does blend.
        wash = max(1, width // 2)
        layer = pygame.Surface((wash, height), pygame.SRCALPHA)
        for col in range(wash):
            alpha = int(46 * (1.0 - col / wash) ** 2)
            pygame.draw.line(layer, (*tint, alpha), (col, 0), (col, height))
        face.blit(layer, (0, 0))
        pygame.draw.line(face, blend(base, (255, 255, 255), 0.22),
                         (self.gap, 0), (width - self.gap, 0))
        mask = pygame.Surface((width, height), pygame.SRCALPHA)
        pygame.draw.rect(mask, (255, 255, 255, 255), mask.get_rect(),
                         border_radius=self.gap)
        face.blit(mask, (0, 0), special_flags=pygame.BLEND_RGBA_MIN)
        self._glass_cache[key] = face
        return face

    def _role(self, role: str, fallback: int) -> int:
        """A font size from Layout's role scale, or *fallback*.

        Layout may not expose ``font_size`` (it is an additive part of the
        visual refresh), and a role it does not know should not be fatal.
        """
        sizer = getattr(self.layout, "font_size", None)
        if callable(sizer):
            try:
                return max(8, int(sizer(role)))
            except Exception:  # noqa: BLE001 - an unknown role is not fatal
                pass
        return fallback

    # ── Top bar: the scoreboard ───────────────────────────────────────────────

    def draw_top_bar(self, surface: pygame.Surface, world: GameWorld) -> None:
        """SCORE as the hero number, HIGH/LEVEL secondary, MODE as a pill."""
        theme = self.theme
        bar = pygame.Rect(0, 0, self.layout.maze_w, self.layout.top_bar)
        surface.fill(theme.background, bar)

        pad = self.pad
        # Everything on the bar sits on the hero number's baseline, so the
        # labels and the smaller values line up along the bottom of the text
        # instead of drifting around the vertical centre.
        baseline = bar.centery + self.bar_hero_font.get_ascent() // 2

        high = max(world.high_score, world.score)
        x = pad
        x = self._stat(surface, "SCORE", f"{world.score}", self.bar_hero_font,
                       self.text_bright, x, baseline)
        x = self._stat(surface, "HIGH", f"{high}", self.bar_font,
                       theme.text, x + pad * 2, baseline)
        x = self._stat(surface, "LEVEL", f"{world.level}", self.bar_font,
                       theme.text, x + pad * 2, baseline)
        x = self._pellet_ring(surface, world, bar, baseline, x + pad * 2)

        pill = self._mode_pill(surface, world, bar)
        self._sample_score(world)
        room = pill.left - pad * 2 - (x + pad * 2)
        if room >= SPARK_MIN_W:
            self._sparkline(surface, bar, x + pad * 2, min(room, SPARK_MAX_W))

        # A hairline along the bottom edge separates the scoreboard from the
        # maze without spending a whole row of pixels on a border.
        rule = theme.accent if self.deluxe else self.accent_dim
        pygame.draw.line(surface, rule, (0, bar.bottom - 1), (bar.right, bar.bottom - 1))

    def _stat(
        self,
        surface: pygame.Surface,
        label: str,
        value: str,
        font: pygame.font.Font,
        colour: Colour,
        x: int,
        baseline: int,
    ) -> int:
        """One ``LABEL value`` pair on the shared baseline; returns the next x."""
        x = self.text.baseline(
            surface, self.bar_label_font, label, self.theme.text_dim, x, baseline
        )
        return self.text.baseline(surface, font, value, colour, x + self.gap, baseline)

    def _mode_pill(
        self, surface: pygame.Surface, world: GameWorld, bar: pygame.Rect
    ) -> pygame.Rect:
        """The mode chip: a status pill with a ring gauge while a power pellet burns.

        While a power pellet is active the scatter/chase schedule is frozen, so
        showing the remaining fright time there is strictly more informative
        than repeating a mode that cannot change — and a ring that unwinds with
        the fraction left turns the number into something readable at a glance.
        """
        text, colour, fraction = self._mode_state(world)
        font = self.bar_font
        label = self.text.get(font, text, colour)

        dot_r = max(2, bar.height // 10)
        height = max(font.get_height() + 4, int(bar.height * 0.62))
        # The ring and the dot share one slot, so the pill is the same width
        # whether or not a pellet is burning and nothing shifts when it ends.
        ring_r = max(dot_r + 2, height // 2 - 3)
        width = label.get_width() + ring_r * 2 + self.pad * 2 + self.gap
        pill = pygame.Rect(
            bar.right - self.pad - width, bar.centery - height // 2, width, height
        )
        radius = height // 2

        pygame.draw.rect(surface, blend(self.theme.background, colour, 0.18), pill,
                         border_radius=radius)
        pygame.draw.rect(surface, colour, pill, 1, border_radius=radius)

        centre = (pill.left + self.pad + ring_r, pill.centery)
        if fraction is None:
            pygame.draw.circle(surface, colour, centre, dot_r)
        else:
            self._ring(surface, centre, ring_r, colour, fraction)
        surface.blit(label, (centre[0] + ring_r + self.gap,
                             pill.centery - label.get_height() // 2))
        return pill

    def _pellet_ring(
        self, surface: pygame.Surface, world: GameWorld, bar: pygame.Rect,
        baseline: int, x: int,
    ) -> int:
        """Pellets left as a ring plus the count; returns the next x.

        The same gauge as the power timer, so the two read alike: the arc is
        what is *left* and it unwinds toward the finish of the level.  It is
        pellet-coloured (warm white), keeping the power ring's yellow free to
        mean "a power pellet is burning".
        """
        total = len(world.maze.pellets) + len(world.maze.power_pellets)
        if not total:
            return x
        radius = max(6, int(bar.height * 0.3))
        centre = (x + radius, bar.centery)
        self._ring(surface, centre, radius, self.theme.pellet,
                   world.pellets_left / total)
        return self._stat(surface, "LEFT", f"{world.pellets_left}", self.bar_font,
                          self.theme.text, centre[0] + radius + self.gap, baseline)

    def _sample_score(self, world: GameWorld) -> None:
        """Record the cumulative score once per :data:`SPARK_INTERVAL` of play.

        Keyed to ``world.elapsed`` rather than the wall clock, so a pause
        freezes the graph instead of filling it with a flat line.  A restart
        sends ``elapsed`` backwards, which is the cue to start over.
        """
        if world.elapsed < self._score_clock:
            self._score_marks.clear()
            self._score_clock = 0.0
        if not self._score_marks or world.elapsed - self._score_clock >= SPARK_INTERVAL:
            self._score_clock = world.elapsed
            self._score_marks.append(world.score)
            del self._score_marks[:-(SPARK_SAMPLES + 1)]

    def _sparkline(
        self, surface: pygame.Surface, bar: pygame.Rect, x: int, width: int
    ) -> None:
        """Points scored per second, as a filled line, plus a trend arrow.

        The cumulative score only ever climbs, so plotting it shows nothing;
        the per-second gain is what rises and falls with how well the run is
        going.  The arrow compares the last third of the window with the
        third before it.
        """
        marks = self._score_marks
        gains = [b - a for a, b in zip(marks, marks[1:])]
        arrow_w = self.gap * 3
        box = pygame.Rect(x, bar.top + bar.height // 5, width - arrow_w - self.gap,
                          bar.height - 2 * (bar.height // 5))
        accent = self.theme.accent

        pygame.draw.line(surface, self.track, box.bottomleft, box.bottomright)
        if len(gains) < 2:
            return
        peak = max(max(gains), 1)
        n = len(gains)
        pts = [
            # Newest sample pinned to the right edge; history grows leftward.
            (box.right - box.width * (n - 1 - i) / (SPARK_SAMPLES - 1),
             box.bottom - (box.height - 1) * g / peak)
            for i, g in enumerate(gains)
        ]
        fill = pts + [(pts[-1][0], box.bottom), (pts[0][0], box.bottom)]
        pygame.draw.polygon(surface, blend(self.theme.background, accent, 0.22), fill)
        pygame.draw.lines(surface, accent, False, pts, 2 if self.deluxe else 1)
        end = (round(pts[-1][0]), round(pts[-1][1]))
        pygame.draw.circle(surface, self.text_bright, end, 2)

        # Trend: recent third vs the third before it.
        third = max(1, n // 3)
        recent, before = sum(gains[-third:]), sum(gains[-2 * third:-third])
        cx, cy = box.right + self.gap + arrow_w // 2, bar.centery
        h = arrow_w // 2 + 1
        if recent > before:
            tri, col = [(cx - h, cy + h // 2), (cx + h, cy + h // 2), (cx, cy - h)], self.success
        elif recent < before:
            tri, col = [(cx - h, cy - h // 2), (cx + h, cy - h // 2), (cx, cy + h)], self.theme.danger
        else:
            pygame.draw.line(surface, self.theme.text_dim, (cx - h, cy), (cx + h, cy), 2)
            return
        pygame.draw.polygon(surface, col, tri)

    def _ring(
        self,
        surface: pygame.Surface,
        centre: Tuple[int, int],
        radius: int,
        colour: Colour,
        fraction: float,
    ) -> None:
        """A ring gauge: dim track, bright arc sweeping clockwise from 12 o'clock.

        The arc is a polyline rather than ``pygame.draw.arc``, whose thick
        strokes break into gaps.  It shrinks as the pellet runs out; a nearly
        spent pellet keeps a short stub rather than vanishing.
        """
        thick = max(2, radius // 3)
        track = blend(self.theme.background, colour, 0.35)
        pygame.draw.circle(surface, track, centre, radius, thick)
        fraction = max(0.0, min(1.0, fraction))
        steps = max(2, int(48 * fraction))
        sweep = math.tau * max(fraction, 0.04)
        mid = radius - thick / 2
        points = [
            (centre[0] + mid * math.sin(sweep * i / steps),
             centre[1] - mid * math.cos(sweep * i / steps))
            for i in range(steps + 1)
        ]
        pygame.draw.lines(surface, colour, False, points, thick)
        for end in (points[0], points[-1]):
            pygame.draw.circle(surface, colour, (round(end[0]), round(end[1])),
                               thick // 2)

    def _mode_state(self, world: GameWorld) -> Tuple[str, Colour, Optional[float]]:
        """Pill text, colour and (only while powered) its fill fraction."""
        theme = self.theme
        if world.power_active:
            duration = max(0.001, float(getattr(world.rules, "power_duration", 7.0)))
            return (f"POWER {world.power_timer:.1f}s", theme.pacman,
                    world.power_timer / duration)
        if world.mode.value == "chase":
            return ("CHASE", theme.danger, None)
        return ("SCATTER", theme.accent, None)

    # ── Bottom bar: lives + key hint ──────────────────────────────────────────

    def draw_bottom_bar(
        self, surface: pygame.Surface, world: GameWorld, hint: str = ""
    ) -> None:
        """Remaining lives as little Pac-Man icons, then the key reminder."""
        theme = self.theme
        top = self.layout.bottom_bar_y
        bar = pygame.Rect(0, top, self.layout.maze_w, self.layout.bottom_bar)
        surface.fill(theme.background, bar)
        pygame.draw.line(surface, self.accent_dim, (0, bar.top), (bar.right, bar.top))

        radius = max(4, int(self.layout.tile * 0.32))
        x = self.pad + radius
        # world.lives counts the life in play as well, so the icons show the
        # *spare* lives — which is what the arcade cabinet does.
        for _ in range(max(0, world.lives - 1)):
            if self.deluxe:
                # A halo instead of a real glow pass: one extra circle, no
                # surface, and it disappears cleanly in LITE.
                pygame.draw.circle(surface, blend(theme.background, theme.pacman, 0.30),
                                   (x, bar.centery), radius + 2)
            draw_pacman_icon(surface, (x, bar.centery), radius, theme.pacman)
            x += radius * 2 + self.gap

        # The hint is reference material, not information: keep it dim so it
        # stops competing with the maze, and trim rather than drop it when the
        # window is narrow.
        available = bar.right - self.pad - (x + self.pad)
        text = _ellipsize(self.small_font, hint or KEY_HINT, available)
        if text and available > 40:
            self.text.right(
                surface, self.small_font, text, theme.text_dim, bar.right - self.pad,
                bar.centery - self.small_font.get_height() // 2,
            )

    # ── Right panel: one card per behaving agent ──────────────────────────────

    def draw_panel(
        self,
        surface: pygame.Surface,
        world: GameWorld,
        brains: Sequence[Any] = (),
        tab: str = "trees",
    ) -> None:
        """The side column: a tab dock, one view, and the world footer.

        ``brains`` may be empty — the game runs perfectly well with no trees
        attached — in which case the TREES view falls back to plain ghost
        state so the column is never a blank rectangle.
        """
        layout, theme = self.layout, self.theme
        if layout.panel_w <= 0:
            return
        if tab not in dict(PANEL_TABS):
            tab = "trees"

        panel = pygame.Rect(layout.panel_x, 0, layout.panel_w, layout.height)
        surface.fill(theme.panel_bg, panel)
        pygame.draw.line(surface, theme.panel_border, panel.topleft, panel.bottomleft, 2)

        pad = self.pad
        x = panel.left + pad
        inner_w = panel.width - pad * 2
        rows = self._agent_rows(world, brains)

        # Game time, not wall time: a pause freezes the lift where it is.
        self._lift_dt = max(0.0, min(0.1, world.elapsed - self._lift_clock))
        self._lift_clock = world.elapsed

        caption = f"{len(rows)} AGENTS" if tab == "trees" else ""
        y = self._panel_heading(surface, x, pad, inner_w, tab, caption)

        # Reserve the footer first: world context that scrolls off the bottom
        # is worse than a card that does, because the footer is the only
        # place the phase and the pellet count appear at all.
        floor = panel.bottom - pad - self._footer_h()
        body = pygame.Rect(panel.left + 2, y, panel.width - 2, max(0, floor - y))

        if self.deluxe and self._shown_tab not in (None, tab) and body.height > 0:
            self._fade_from = self._snapshot(
                surface, self._shown_tab, world, brains, rows, x, y, inner_w, floor, body)
            self._fade_start = time.monotonic()
        self._shown_tab = tab

        self._draw_body(surface, tab, world, brains, rows, x, y, inner_w, floor)
        self._cross_fade(surface, body)

        self._panel_footer(surface, world, x, panel.bottom - pad, inner_w)

    def _draw_body(self, surface, tab, world, brains, rows, x, y, inner_w, floor) -> None:
        if tab == "stats":
            self._stats_view(surface, world, x, y, inner_w, floor)
        elif tab == "log":
            self._log_view(surface, x, y, inner_w, floor)
        else:
            self._trees_view(surface, world, brains, rows, x, y, inner_w, floor)

    # ── Tab cross-fade ────────────────────────────────────────────────────────

    def _snapshot(self, surface, tab, world, brains, rows, x, y, inner_w, floor, body):
        """The outgoing tab's body as a still, taken once when the tab changes.

        Drawn onto a scratch surface at the same coordinates, so the views need
        no idea they are being photographed.  The lift easing is saved and
        restored: drawing the old view must not advance it a second time.
        """
        scratch = pygame.Surface(surface.get_size())
        scratch.fill(self.theme.panel_bg)
        lifts = dict(self._lift)
        self._draw_body(scratch, tab, world, brains, rows, x, y, inner_w, floor)
        self._lift = lifts
        return scratch.subsurface(body).copy()

    def _cross_fade(self, surface: pygame.Surface, body: pygame.Rect) -> None:
        """Lay the fading still of the old tab over the freshly drawn new one."""
        if self._fade_from is None:
            return
        t = (time.monotonic() - self._fade_start) / TAB_FADE
        if t >= 1.0 or self._fade_from.get_size() != body.size:
            self._fade_from = None
            return
        t = 1.0 - (1.0 - t) ** 2                       # ease-out
        self._fade_from.set_alpha(int(255 * (1.0 - t)))
        surface.blit(self._fade_from, body.topleft)

    def _trees_view(self, surface, world, brains, rows, x, y, inner_w, floor) -> None:
        if not brains:
            y = self._blit_wrapped(
                surface, "no trees attached - showing raw ghost state",
                x, y, inner_w, self.panel_small_font, self.theme.text_dim,
            ) + self.gap
        for name, brain, ghost in rows:
            if y + self.card_h > floor:
                break
            lift = self._card_lift(world, name, ghost)
            if lift > 0:
                self._lift_shadow(surface, x, y, inner_w, lift)
            self._draw_agent_card(surface, world, name, brain, ghost, x,
                                  y - lift, inner_w)
            y += self.card_h + self.pad
        if y + self._legend_h() <= floor:
            self._status_legend(surface, x, y + self.gap, inner_w)

    # ── Proximity lift ────────────────────────────────────────────────────────

    def _card_lift(self, world: GameWorld, name: str, ghost: Optional[Ghost]) -> int:
        """Pixels a card is raised: the closer a *hostile* ghost, the higher.

        The same proximity the card's strip shows, but it reaches the eye
        without reading: a ghost closing in physically comes forward.  Edible,
        parked and eaten ghosts cannot hurt Pac-Man, so they never lift, and
        neither does Pac-Man's own card.  The rise is capped well inside the
        gap between cards (it must never touch the neighbour) and eased in
        DELUXE; LITE moves in one step.
        """
        target = 0.0
        if ghost is not None and ghost.is_hostile:
            t = (self._proximity(world, ghost) - LIFT_FROM) / (LIFT_TO - LIFT_FROM)
            t = max(0.0, min(1.0, t))
            target = t * t * (3.0 - 2.0 * t)          # smoothstep
        now = self._lift.get(name, 0.0)
        if self.deluxe:
            step = min(1.0, LIFT_RATE * self._lift_dt)
            now += (target - now) * step
        else:
            now = 1.0 if target >= 0.5 else 0.0
        self._lift[name] = now
        return int(round(now * min(4, self.pad // 2)))

    def _lift_shadow(
        self, surface: pygame.Surface, x: int, y: int, width: int, lift: int
    ) -> None:
        """A dark slab under a raised card, so it reads as off the surface."""
        shade = blend(self.theme.panel_bg, (0, 0, 0), 0.55)
        pygame.draw.rect(surface, shade,
                         pygame.Rect(x + 2, y + 2, width - 4, self.card_h),
                         border_radius=self.gap)

    # ── STATS and LOG views ───────────────────────────────────────────────────

    def _stats_view(self, surface, world, x, y, width, floor) -> None:
        """Run numbers as glass tiles: a big value over a small label."""
        theme = self.theme
        ghosts = world.ghosts
        total = len(world.maze.pellets) + len(world.maze.power_pellets)
        eaten = max(0, total - world.pellets_left)
        tiles = (
            ("SCORE", f"{world.score}", self.text_bright),
            ("HIGH", f"{max(world.high_score, world.score)}", theme.text),
            ("LEVEL", f"{world.level}", theme.text),
            ("LIVES", f"{max(0, world.lives)}", self.warning),
            ("HUNTING", f"{sum(g.is_hostile for g in ghosts)}", theme.danger),
            ("EDIBLE", f"{sum(g.is_edible for g in ghosts)}", self.success),
        )
        cols = 2
        tile_w = (width - self.gap) // cols
        tile_h = self.panel_name_font.get_height() + self.panel_small_font.get_height() \
            + self.pad + self.gap
        for i, (label, value, colour) in enumerate(tiles):
            col, row = i % cols, i // cols
            box = pygame.Rect(x + col * (tile_w + self.gap),
                              y + row * (tile_h + self.gap), tile_w, tile_h)
            if box.bottom > floor:
                return
            surface.blit(self._glass(box.width, box.height, colour), box.topleft)
            self.text.blit(surface, self.panel_name_font, value, colour,
                           (box.left + self.pad, box.top + self.gap))
            self.text.blit(surface, self.panel_small_font, label, theme.text_dim,
                           (box.left + self.pad,
                            box.bottom - self.panel_small_font.get_height() - self.gap))
        y += ((len(tiles) + cols - 1) // cols) * (tile_h + self.gap) + self.gap

        # Board progress: the same meter the Pac-Man card uses, but bigger.
        if y + self.row_h + _STRIP_H * 2 > floor or not total:
            return
        self.text.blit(surface, self.panel_small_font, "BOARD CLEARED",
                       theme.text_dim, (x, y))
        self.text.right(surface, self.panel_small_font, f"{eaten}/{total}",
                        theme.text, x + width, y)
        y += self.panel_small_font.get_height() + self.gap
        track = pygame.Rect(x, y, width, _STRIP_H * 2)
        pygame.draw.rect(surface, self.track, track, border_radius=_STRIP_H)
        done = int(width * eaten / total)
        if done > 0:
            pygame.draw.rect(surface, theme.pacman, pygame.Rect(x, y, done, track.height),
                             border_radius=_STRIP_H)

    def record(self, world: GameWorld, events: Sequence[Any]) -> None:
        """Remember this frame's notable game events for the LOG tab.

        Called once per frame by the renderer.  Pellets are skipped (hundreds
        per level would bury everything else).  ``elapsed`` going backwards
        means a restart, which starts a clean log.
        """
        if world.elapsed < self._log_clock:
            self._log.clear()
        self._log_clock = world.elapsed
        for event in events:
            if event.kind in LOG_SKIP:
                continue
            text = event.message or event.kind.replace("_", " ").upper()
            self._log.append((world.elapsed, event.kind, text))

    def _log_view(self, surface, x, y, width, floor) -> None:
        """Newest event first, each with its time and a kind-coloured dot."""
        theme = self.theme
        if not self._log:
            self._blit_wrapped(surface, "no events yet - eat a power pellet",
                               x, y, width, self.panel_small_font, theme.text_dim)
            return
        line = self.panel_font.get_height() + self.gap
        for stamp, kind, text in reversed(self._log):
            if y + line > floor:
                break
            colour = self._event_colour(kind)
            pygame.draw.circle(surface, colour,
                               (x + _DOT, y + self.panel_font.get_height() // 2), _DOT)
            cursor = x + _DOT * 2 + self.gap
            self.text.right(surface, self.panel_small_font, f"{stamp:.1f}s",
                            theme.text_dim, x + width, y + 1)
            room = width - (cursor - x) - self.panel_small_font.size("000.0s")[0] - self.gap
            self.text.blit(surface, self.panel_font,
                           _ellipsize(self.panel_font, text, max(20, room)),
                           theme.text, (cursor, y))
            y += line

    def _event_colour(self, kind: str) -> Colour:
        theme = self.theme
        return {
            "power": theme.pacman, "ghost_eaten": self.success,
            "pacman_caught": theme.danger, "game_over": theme.danger,
            "extra_life": self.success, "level_clear": theme.accent,
            "level_start": theme.accent, "mode": theme.text_dim,
        }.get(kind, theme.text_dim)

    # ── The tab dock ──────────────────────────────────────────────────────────

    def tab_at(self, pos: Tuple[int, int]) -> Optional[str]:
        """The tab under a mouse position, from the last drawn dock."""
        for key, rect in self._tab_rects.items():
            if rect.collidepoint(pos):
                return key
        return None

    def _agent_rows(
        self, world: GameWorld, brains: Sequence[Any]
    ) -> List[Tuple[str, Any, Optional[Ghost]]]:
        """Pair every brain with its ghost (``None`` for Pac-Man's autopilot).

        With no brains at all we still list the ghosts, so the panel reflects
        the world rather than the behavior layer's absence.
        """
        if not brains:
            return [(ghost.name, None, ghost) for ghost in world.ghosts]
        rows: List[Tuple[str, Any, Optional[Ghost]]] = []
        for brain in brains:
            name = getattr(brain, "name", "?")
            rows.append((name, brain, world.ghost(name)))
        return rows

    def _draw_agent_card(
        self,
        surface: pygame.Surface,
        world: GameWorld,
        name: str,
        brain: Any,
        ghost: Optional[Ghost],
        x: int,
        y: int,
        width: int,
    ) -> None:
        """One agent's record card, drawn into a fixed :attr:`card_h` box."""
        theme = self.theme
        colour = theme.agent_colour(name)
        card = pygame.Rect(x, y, width, self.card_h)

        # The card gives the block an edge to align against; the identity strip
        # down its left side is what makes the panel row and the sprite in the
        # maze read as the same ghost from across the screen.
        surface.blit(self._glass(card.width, card.height, colour), card.topleft)
        if self.deluxe:
            pygame.draw.rect(surface, blend(self.card_bg, colour, 0.22), card, 1,
                             border_radius=self.gap)
        pygame.draw.rect(surface, colour,
                         pygame.Rect(card.left, card.top, _STRIP_W, card.height),
                         border_top_left_radius=self.gap,
                         border_bottom_left_radius=self.gap)

        left = card.left + _STRIP_W + self.pad
        right = card.right - self.pad
        row_y = card.top + self.pad

        # ── Row 1: identity ───────────────────────────────────────────────────
        icon = pygame.Rect(left, row_y + 2, _ICON, _ICON)
        if ghost is None:
            draw_pacman_icon(surface, icon.center, _ICON // 2, colour)
        else:
            draw_ghost_icon(surface, icon, colour, theme.ghost_eye)
        self.text.blit(surface, self.panel_name_font, name.upper(), colour,
                       (icon.right + self.gap, row_y))
        state = ghost.state.value.upper().replace("_", " ") if ghost else "AUTOPILOT"
        self.text.right(surface, self.panel_small_font, state, theme.text_dim, right,
                        row_y + 4)
        row_y += self.name_h + self.gap

        # ── Row 2: where in the tree ──────────────────────────────────────────
        # Rendered as a path (``branch › node``) rather than two labelled rows:
        # the branch is context and the leaf is the live thing, and that is
        # exactly how a chevron reads.
        branch = _text_of(brain, "branch")
        node = _text_of(brain, "active_node")
        if not branch and not node:
            # No tree on this agent: a bare "- > -" path reads as a bug rather
            # than as an absence, so say what is actually going on.
            self.text.blit(surface, self.panel_font, "no tree attached",
                           theme.text_dim, (left, row_y))
        else:
            cursor = left + self.text.blit(surface, self.panel_font, branch or "-",
                                           theme.text, (left, row_y))
            cursor += self.text.blit(surface, self.panel_font, " > ", self.accent_dim,
                                     (cursor, row_y))
            self.text.blit(
                surface, self.panel_font,
                _ellipsize(self.panel_font, node or "-", max(20, right - cursor)),
                theme.accent, (cursor, row_y),
            )
        row_y += self.row_h + self.gap

        # ── Row 3: tick status and target tile ────────────────────────────────
        status = _status_name(brain)
        status_colour = theme.status_colour(status)
        chip = pygame.Rect(left, row_y + self.row_h // 2 - _DOT, _DOT * 2, _DOT * 2)
        pygame.draw.rect(surface, status_colour, chip, border_radius=_DOT)
        self.text.blit(surface, self.panel_font, status, status_colour,
                       (chip.right + self.gap, row_y))
        # Pac-Man aims at tiles too when his autopilot is on, and `target`
        # lives on Agent, so fall back to the brain's own agent.
        target = ghost.target if ghost is not None else _target_of(brain)
        label = "-" if target is None else f"({target[0]}, {target[1]})"
        self.text.right(surface, self.panel_font, label, theme.text_dim, right, row_y)
        row_y += self.row_h + self.gap

        self._proximity_strip(surface, world, ghost, colour, left, row_y, right - left)

    def _proximity_strip(
        self,
        surface: pygame.Surface,
        world: GameWorld,
        ghost: Optional[Ghost],
        colour: Colour,
        x: int,
        y: int,
        width: int,
    ) -> None:
        """A slim meter closing the card.

        For a ghost it is the ``distance_to_pacman`` blackboard mirror made
        visible — full means "on top of him" — because that single number is
        what most of the guards in every ghost tree branch on, and watching it
        fill is what makes a chase branch flip look inevitable rather than
        random.  Its colour carries who is hunting whom: red while the ghost is
        dangerous, mint while it is edible, dim while it is out of play.  For
        Pac-Man's autopilot, where the distance is trivially zero, it shows the
        board cleared instead.
        """
        theme = self.theme
        track = pygame.Rect(x, y, width, _STRIP_H)
        pygame.draw.rect(surface, self.track, track, border_radius=_STRIP_H // 2)

        if ghost is None:
            total = len(world.maze.pellets) + len(world.maze.power_pellets)
            fraction = 1.0 - world.pellets_left / total if total else 0.0
            fill_colour = colour
        elif ghost.is_edible:
            fraction = self._proximity(world, ghost)
            fill_colour = self.success
        elif ghost.is_hostile:
            fraction = self._proximity(world, ghost)
            fill_colour = theme.danger
        else:
            # IN_HOUSE or EATEN: the ghost cannot touch Pac-Man, so a threat
            # reading would be a lie.  Show it as parked.
            fraction, fill_colour = 0.0, theme.text_dim

        filled = max(0, int(width * max(0.0, min(1.0, fraction))))
        if filled > 0:
            pygame.draw.rect(surface, fill_colour,
                             pygame.Rect(x, y, filled, _STRIP_H),
                             border_radius=_STRIP_H // 2)

    @staticmethod
    def _proximity(world: GameWorld, ghost: Ghost) -> float:
        """1.0 when the ghost is on Pac-Man's tile, 0.0 beyond the range."""
        distance = manhattan(ghost.tile, world.pacman.tile)
        return 1.0 - min(1.0, distance / PROXIMITY_RANGE)

    # ── Panel chrome ──────────────────────────────────────────────────────────

    def _legend_h(self) -> int:
        small = self.panel_small_font.get_height()
        return small * 2 + self.gap * 2

    def _status_legend(
        self, surface: pygame.Surface, x: int, y: int, width: int
    ) -> None:
        """A key for the status chips.

        Every card already spells its status out, so this is not what makes the
        colour accessible — it is what makes it *fast*.  Read the legend once
        and the four cards can then be scanned by colour alone, which is the
        difference between watching the trees and reading them.
        """
        theme = self.theme
        font = self.panel_small_font
        self.text.blit(surface, font, "TICK STATUS", theme.text_dim, (x, y))
        y += font.get_height() + self.gap

        # Laid out left to right on one row; the four statuses are short enough
        # that the panel's minimum width always fits them.
        cursor = x
        for status in ("RUNNING", "SUCCESS", "FAILURE", "IDLE"):
            colour = theme.status_colour(status)
            dot = pygame.Rect(cursor, y + font.get_height() // 2 - 3, 6, 6)
            pygame.draw.rect(surface, colour, dot, border_radius=3)
            cursor = dot.right + self.gap
            cursor += self.text.blit(surface, font, status, theme.text_dim, (cursor, y))
            cursor += self.pad


    def _panel_heading(
        self, surface: pygame.Surface, x: int, y: int, width: int, tab: str,
        caption: str = "",
    ) -> int:
        """The tab dock, an optional caption beside it, and an accent rule.

        The dock is one rounded pill; the active tab is a filled segment in the
        accent colour and the others are plain dim text, so the current view is
        a shape and not only a colour.  Each tab's box is remembered for
        :meth:`tab_at`.
        """
        theme = self.theme
        font = self.panel_small_font
        labels = [(key, self.text.get(font, label, theme.text_dim)) for key, label in PANEL_TABS]
        seg_pad = self.pad
        height = font.get_height() + self.gap * 2
        dock_w = sum(img.get_width() + seg_pad * 2 for _, img in labels)
        dock = pygame.Rect(x, y, dock_w, height)
        pygame.draw.rect(surface, self.card_bg, dock, border_radius=height // 2)
        pygame.draw.rect(surface, theme.panel_border, dock, 1, border_radius=height // 2)

        self._tab_rects = {}
        cursor = dock.left
        for key, img in labels:
            seg = pygame.Rect(cursor, dock.top, img.get_width() + seg_pad * 2, height)
            self._tab_rects[key] = seg
            if key == tab:
                pygame.draw.rect(surface, theme.accent, seg.inflate(-4, -4),
                                 border_radius=(height - 4) // 2)
                img = self.text.get(font, dict(PANEL_TABS)[key], theme.background)
            surface.blit(img, (seg.left + seg_pad, seg.centery - img.get_height() // 2))
            cursor = seg.right

        if caption:
            self.text.right(surface, font, caption, theme.text_dim, x + width,
                            dock.centery - font.get_height() // 2)
        y = dock.bottom + self.gap
        pygame.draw.line(surface, theme.panel_border, (x, y), (x + width, y))
        return y + self.pad

    def _footer_h(self) -> int:
        """Pixels the footer needs, including its separating rule."""
        return len(_FOOTER_LABELS) * (self.panel_font.get_height() + 1) + self.pad

    def _panel_footer(
        self, surface: pygame.Surface, world: GameWorld, x: int, bottom: int, width: int
    ) -> None:
        """World-level context the per-agent cards cannot show on their own.

        Labels sit on the left, values are right-aligned to the panel edge: the
        four values then form a column you can read straight down, which loose
        ``label value`` text never manages.
        """
        theme = self.theme
        power = f"  +{world.power_timer:.1f}s" if world.power_active else ""
        values = (
            (world.phase.value, theme.danger
             if world.phase is GamePhase.GAME_OVER else theme.text),
            (world.mode.value + power, theme.text),
            (f"{world.pellets_left}", theme.text),
            (f"{world.elapsed:.1f}s", theme.text),
        )
        line_h = self.panel_font.get_height() + 1
        y = bottom - len(values) * line_h
        pygame.draw.line(surface, theme.panel_border, (x, y - self.gap),
                         (x + width, y - self.gap))
        for label, (value, colour) in zip(_FOOTER_LABELS, values):
            self.text.blit(surface, self.panel_font, label, theme.text_dim, (x, y))
            self.text.right(
                surface, self.panel_font,
                _ellipsize(self.panel_font, value, width - 70), colour, x + width, y,
            )
            y += line_h

    def _blit_wrapped(
        self,
        surface: pygame.Surface,
        text: str,
        x: int,
        y: int,
        width: int,
        font: pygame.font.Font,
        colour: Colour,
    ) -> int:
        """Naive greedy word wrap — enough for the one or two notes we show."""
        words = text.split()
        line = ""
        for word in words:
            candidate = f"{line} {word}".strip()
            if font.size(candidate)[0] <= width or not line:
                line = candidate
                continue
            self.text.blit(surface, font, line, colour, (x, y))
            y += font.get_height()
            line = word
        if line:
            self.text.blit(surface, font, line, colour, (x, y))
            y += font.get_height()
        return y


# ── Card metrics (shared by both presets so F3 never reflows anything) ─────────

#: Width of the ghost-coloured identity strip down the left of a card.
_STRIP_W = 4
#: Height of the proximity meter closing a card.
_STRIP_H = 5
#: Side of the little ghost swatch on a card's name row.
_ICON = 13
#: Radius of the status chip's dot.
_DOT = 4

_FOOTER_LABELS = ("PHASE", "MODE", "PELLETS", "ELAPSED")


# ── Small shared sprites (used by the HUD and by the panel swatches) ──────────

def draw_pacman_icon(
    surface: pygame.Surface, centre: Tuple[int, int], radius: int, colour: Colour
) -> None:
    """A tiny Pac-Man with a fixed open mouth — the life counter icon."""
    import math

    cx, cy = centre
    points = [(cx, cy)]
    start, end = math.radians(30), math.radians(330)
    steps = 14
    for i in range(steps + 1):
        angle = start + (end - start) * i / steps
        points.append((cx + radius * math.cos(angle), cy - radius * math.sin(angle)))
    pygame.draw.polygon(surface, colour, points)


def draw_ghost_icon(
    surface: pygame.Surface, rect: pygame.Rect, colour: Colour, eye: Colour
) -> None:
    """A flat ghost silhouette that fits an arbitrary rect (panel swatch)."""
    radius = rect.width // 2
    pygame.draw.circle(surface, colour, (rect.centerx, rect.top + radius), radius)
    pygame.draw.rect(
        surface, colour,
        pygame.Rect(rect.left, rect.top + radius, rect.width, rect.height - radius),
    )
    dot = max(1, rect.width // 7)
    pygame.draw.circle(surface, eye, (rect.centerx - dot, rect.top + radius), dot)
    pygame.draw.circle(surface, eye, (rect.centerx + dot, rect.top + radius), dot)


# ── Brain introspection helpers (duck-typed on purpose) ───────────────────────

def _colour(theme: Theme, name: str, fallback: Colour) -> Colour:
    """A palette field, or *fallback* if this Theme predates it."""
    value = getattr(theme, name, None)
    return value if isinstance(value, tuple) and len(value) == 3 else fallback


def _text_of(brain: Any, attribute: str) -> str:
    """Read a string attribute off a brain, tolerating ``None`` and absence."""
    if brain is None:
        return ""
    value = getattr(brain, attribute, "")
    return "" if value is None else str(value)


def _status_name(brain: Any) -> str:
    """Normalise ``brain.status`` to a ``NodeStatus`` *name*.

    ``AgentBrain.status`` is a ``bteng.NodeStatus``, but a test double may hand
    us a bare string; both end up as e.g. ``"RUNNING"`` so the theme lookup and
    the label agree.
    """
    if brain is None:
        return "IDLE"
    status = getattr(brain, "status", None)
    if status is None:
        return "IDLE"
    return str(getattr(status, "value", status)).upper()


def _ellipsize(font: pygame.font.Font, text: str, width: int) -> str:
    """Trim *text* until it fits *width*, marking the cut with an ellipsis."""
    if font.size(text)[0] <= width:
        return text
    trimmed = text
    while trimmed and font.size(trimmed + "...")[0] > width:
        trimmed = trimmed[:-1]
    return trimmed + "..."


def _target_of(brain: Any) -> Optional[Tuple[int, int]]:
    """The tile a non-ghost brain (Pac-Man's autopilot) is steering toward."""
    return getattr(getattr(brain, "agent", None), "target", None)
