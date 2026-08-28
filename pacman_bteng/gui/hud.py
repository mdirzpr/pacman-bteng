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
        self._stat(surface, "LEVEL", f"{world.level}", self.bar_font,
                   theme.text, x + pad * 2, baseline)

        self._mode_pill(surface, world, bar)

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
    ) -> None:
        """The mode chip: a status pill that drains while a power pellet burns.

        While a power pellet is active the scatter/chase schedule is frozen, so
        showing the remaining fright time there is strictly more informative
        than repeating a mode that cannot change — and filling the pill by the
        fraction left turns the number into something readable at a glance.
        """
        text, colour, fraction = self._mode_state(world)
        font = self.bar_font
        label = self.text.get(font, text, colour)

        dot_r = max(2, bar.height // 10)
        height = max(font.get_height() + 4, int(bar.height * 0.62))
        width = label.get_width() + dot_r * 2 + self.pad * 2 + self.gap
        pill = pygame.Rect(
            bar.right - self.pad - width, bar.centery - height // 2, width, height
        )
        radius = height // 2

        pygame.draw.rect(surface, blend(self.theme.background, colour, 0.18), pill,
                         border_radius=radius)
        if fraction is not None:
            # The meter is inset by a pixel so the rounded border stays crisp
            # over it, and clamped to at least a dot's width so a nearly-spent
            # pellet still shows *something* rather than blinking out.
            filled = pill.inflate(-2, -2)
            filled.width = max(dot_r * 2, int(filled.width * max(0.0, min(1.0, fraction))))
            pygame.draw.rect(surface, blend(self.theme.background, colour, 0.45), filled,
                             border_radius=max(1, radius - 1))
        pygame.draw.rect(surface, colour, pill, 1, border_radius=radius)

        cx = pill.left + self.pad
        pygame.draw.circle(surface, colour, (cx + dot_r, pill.centery), dot_r)
        surface.blit(label, (cx + dot_r * 2 + self.gap,
                             pill.centery - label.get_height() // 2))

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
        self, surface: pygame.Surface, world: GameWorld, brains: Sequence[Any] = ()
    ) -> None:
        """The live behavior-tree column.

        ``brains`` may be empty — the game runs perfectly well with no trees
        attached — in which case the panel falls back to plain ghost state so
        the column is never a blank rectangle.
        """
        layout, theme = self.layout, self.theme
        if layout.panel_w <= 0:
            return

        panel = pygame.Rect(layout.panel_x, 0, layout.panel_w, layout.height)
        surface.fill(theme.panel_bg, panel)
        pygame.draw.line(surface, theme.panel_border, panel.topleft, panel.bottomleft, 2)

        pad = self.pad
        x = panel.left + pad
        inner_w = panel.width - pad * 2
        rows = self._agent_rows(world, brains)

        y = self._panel_heading(surface, x, pad, inner_w, len(rows))
        if not brains:
            y = self._blit_wrapped(
                surface, "no trees attached - showing raw ghost state",
                x, y, inner_w, self.panel_small_font, theme.text_dim,
            ) + self.gap

        # Reserve the footer first: world context that scrolls off the bottom
        # is worse than an agent card that does, because the footer is the only
        # place the phase and the pellet count appear at all.
        floor = panel.bottom - pad - self._footer_h()
        for name, brain, ghost in rows:
            if y + self.card_h > floor:
                break
            self._draw_agent_card(surface, world, name, brain, ghost, x, y, inner_w)
            y += self.card_h + self.pad

        if y + self._legend_h() <= floor:
            self._status_legend(surface, x, y + self.gap, inner_w)

        self._panel_footer(surface, world, x, panel.bottom - pad, inner_w)

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
        pygame.draw.rect(surface, self.card_bg, card, border_radius=self.gap)
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
        self, surface: pygame.Surface, x: int, y: int, width: int, count: int
    ) -> int:
        """Title plus agent count, over an accent rule."""
        theme = self.theme
        heading = self.text.get(self.panel_title_font, "BEHAVIOR TREES", theme.accent)
        surface.blit(heading, (x, y))
        self.text.right(
            surface, self.panel_small_font, f"{count} AGENTS", theme.text_dim,
            x + width, y + heading.get_height() - self.panel_small_font.get_height() - 1,
        )
        y += heading.get_height() + self.gap
        # A full-width hairline for the edge, plus a short thick accent stub
        # under the title: it anchors the heading to the column without turning
        # into a second border.
        pygame.draw.line(surface, theme.panel_border, (x, y), (x + width, y))
        pygame.draw.rect(surface, theme.accent,
                         pygame.Rect(x, y - 1, heading.get_width() // 2, 2))
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
