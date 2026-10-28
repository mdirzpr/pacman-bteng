"""Renderer — draws a whole frame of the game with nothing but pygame.draw.

No image assets, by design: the maze, Pac-Man and the ghosts are all built from
circles, rects and polygons, so the project stays a single ``pip install`` with
no binary files to keep in sync with the code.

Two things are worth knowing before editing this module:

* **Everything is derived from the world, every frame.**  The renderer keeps no
  animation state of its own except the pre-computed wall geometry; the mouth
  angle and the power-pellet blink are functions of ``world.elapsed``.  That
  makes a frame reproducible from the world alone, which is what lets a
  headless smoke test assert on it.
* **Headless is the same code path.**  With ``headless=True`` we set SDL's
  dummy video driver *before* ``pygame.init()`` and then do exactly what the
  windowed path does, flip included.  A test that runs headless therefore
  exercises the real drawing code instead of a stub.
"""

from __future__ import annotations

import math
import os
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Sequence, Tuple

import pygame

from pacman_bteng.game.entities import Agent, Ghost, GhostState, Pacman
from pacman_bteng.game.geometry import Direction, Tile
from pacman_bteng.game.maze import Maze
from pacman_bteng.game.world import GamePhase, GameWorld
from pacman_bteng.gui.hud import Hud
from pacman_bteng.gui.layout import Layout
from pacman_bteng.gui.theme import Colour, Theme, blend

#: Only ``#`` is a drawn wall.  ``X`` is *void* — outside the maze — and must
#: read as background, otherwise the tunnel mouths grow blue blocks.
WALL_CHAR = "#"

#: Mouth chomps per second, and how wide it opens at full gape (degrees).
CHOMP_HZ = 4.5
CHOMP_ANGLE = 62.0

#: Power pellets blink at this rate; the arcade ones do the same.
PELLET_BLINK_HZ = 3.2

#: Screen-space direction angles (degrees, maths convention, y flipped at use).
_FACING: Dict[Direction, float] = {
    Direction.RIGHT: 0.0,
    Direction.UP: 90.0,
    Direction.LEFT: 180.0,
    Direction.DOWN: 270.0,
}


class Renderer:
    """Owns the pygame display and paints one frame per :meth:`draw` call."""

    def __init__(
        self,
        layout: Layout,
        theme: Theme,
        *,
        show_panel: bool = True,
        caption: str = "Pac-Man · BTEng",
        headless: bool = False,
        style: Optional["VisualStyle"] = None,
        history: Optional[Any] = None,
    ) -> None:
        self.layout = layout
        self.theme = theme
        self.show_panel = show_panel and layout.panel_w > 0
        self.headless = headless
        self.style = style if style is not None else getattr(theme, "style", None)

        if headless:
            # MUST happen before pygame.init(): SDL reads the driver name when
            # the video subsystem comes up, not when the mode is set.
            os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

        pygame.init()
        pygame.font.init()
        self.screen = pygame.display.set_mode(layout.size)
        if not headless:
            pygame.display.set_caption(caption)

        self.hud = Hud(layout, theme, history)
        self.overlay_font = self.hud.fonts.at(max(28, int(layout.tile * 2.0)))
        self._closed = False

        # Wall outlines never change while the maze does not, and recomputing
        # ~800 tiles x 4 edges every frame is the one obvious waste in a
        # draw-primitive renderer.  Cache them per maze object.
        self._wall_cache_key: Any = None
        self._wall_rects: List[pygame.Rect] = []
        self._wall_edges: List[Tuple[Tuple[int, int], Tuple[int, int]]] = []
        #: The whole static board (walls + outlines + door + pellets),
        #: pre-rendered once and re-rendered only when a pellet goes.
        self._board: Any = None
        self._board_key: Any = None

        # ── The deluxe effect layer ──────────────────────────────────────────
        # Built only for the deluxe preset, and imported only then: the lite
        # preset should not pay a single cached glow surface for a feature it
        # never draws.
        self.effects = self._build_effects()
        #: Current screen-shake offset, refreshed once per frame.  Applied
        #: through _px()/_tpx() rather than by re-blitting the whole board,
        #: which would cost a full-screen copy every frame for an effect that
        #: is (0, 0) more than 99% of the time.
        self._shake: Tuple[int, int] = (0, 0)
        self._last_elapsed: float = 0.0

    # ── Frame ─────────────────────────────────────────────────────────────────

    def draw(
        self,
        world: GameWorld,
        brains: Sequence[Any] = (),
        *,
        debug: bool = False,
        overlay: str = "",
        events: Sequence[Any] = (),
        panel_tab: str = "trees",
    ) -> None:
        """Paint and present one complete frame.

        *events* is the list :meth:`GameWorld.update` just returned.  Only the
        deluxe effect layer reads it — that is how a burst of particles ends up
        exactly where the ghost was eaten rather than a frame late.
        """
        if self._closed:
            return

        surface = self.screen
        surface.fill(self.theme.background)
        self._advance_effects(world, events)

        if self.effects is not None:
            self.effects.draw_under(surface, world)

        self._draw_board(surface, world)
        for ghost in world.ghosts:
            self._draw_ghost(surface, ghost, world)
        self._draw_pacman(surface, world)

        if debug:
            self._draw_debug(surface, world)

        if self.effects is not None:
            self.effects.draw_over(surface, world)

        self.hud.record(world, events)
        self.hud.draw_top_bar(surface, world)
        self.hud.draw_bottom_bar(surface, world, "")
        if self.show_panel:
            self.hud.draw_panel(surface, world, brains, tab=panel_tab)

        if overlay:
            self._draw_overlay(surface, overlay)

        pygame.display.flip()

    # ── Effects ───────────────────────────────────────────────────────────────

    def _build_effects(self) -> Any:
        """The deluxe overlay, or None for the lite preset.

        A missing effects module is not an error: the game must still start on a
        checkout where only the lite preset exists.
        """
        if not self.is_deluxe:
            return None
        try:
            from pacman_bteng.gui.effects import EffectLayer
        except ImportError:
            return None
        return EffectLayer(self.layout, self.theme)

    @property
    def is_deluxe(self) -> bool:
        return getattr(self.style, "value", "lite") == "deluxe"

    def _advance_effects(self, world: GameWorld, events: Sequence[Any]) -> None:
        """Feed and step the effect layer, and read this frame's shake.

        dt comes from ``world.elapsed`` rather than from a wall clock so the
        effects freeze exactly when the game does — a particle burst that keeps
        expanding behind the PAUSED overlay looks like a bug.
        """
        if self.effects is None:
            self._shake = (0, 0)
            return
        dt = max(0.0, min(world.elapsed - self._last_elapsed, 0.05))
        self._last_elapsed = world.elapsed
        self.effects.feed(world, events)
        self.effects.update(dt)
        self._shake = self.effects.shake()

    def reset_effects(self) -> None:
        """Drop every live effect — called when the player restarts."""
        if self.effects is not None:
            self.effects.reset()
        self._shake = (0, 0)

    # ── Shake-aware geometry ──────────────────────────────────────────────────

    def _px(self, pos: Tuple[float, float]) -> Tuple[int, int]:
        """Pixel centre of a position in tile units, plus any screen shake."""
        x, y = self.layout.centre_px(pos)
        return (x + self._shake[0], y + self._shake[1])

    def _tpx(self, tile: Tuple[int, int]) -> Tuple[int, int]:
        """Top-left pixel of a tile, plus any screen shake."""
        x, y = self.layout.tile_px(tile)
        return (x + self._shake[0], y + self._shake[1])

    def close(self) -> None:
        """Tear the display down.  Safe to call twice."""
        if self._closed:
            return
        self._closed = True
        pygame.display.quit()
        pygame.quit()

    # ── Maze ──────────────────────────────────────────────────────────────────

    def _draw_board(self, surface, world: GameWorld) -> None:
        """Blit the pre-rendered board: walls, outlines, door and pellets.

        WHY THIS IS CACHED
        ------------------
        The maze is ~800 wall tiles, each contributing a fill plus up to four
        outline segments, and the pellet layer is another ~240 circles.  Walking
        all of that every frame was over half the render budget for a picture
        that changes only when a pellet is eaten — a few times a second, not
        sixty.  So the whole static board is drawn once into an opaque surface
        and blitted; the cache key includes the pellet count, and pellets are
        only ever removed within a level, so the count identifies the layout
        exactly.

        The four power pellets stay out of the cache: they pulse, and redrawing
        four circles is cheaper than rebuilding the board sixty times a second.
        """
        board = self._ensure_board(world)
        ox, oy = self.layout.maze_origin
        surface.blit(board, (ox + self._shake[0], oy + self._shake[1]))
        self._draw_power_pellets(surface, world)

    def _ensure_board(self, world: GameWorld):
        """(Re)render the static board surface when anything on it changed."""
        maze = world.maze
        key = (id(maze), self.layout.tile, self.theme.wall, len(world.pellets))
        if key == self._board_key and self._board is not None:
            return self._board

        self._board_key = key
        if self._board is None:
            # Allocated once for the life of the renderer, not per frame.
            self._board = pygame.Surface((self.layout.maze_w, self.layout.maze_h))
            # A colour key, not per-pixel alpha: empty corridor pixels must be
            # transparent so the deluxe layer's bloom - which is painted BEFORE
            # the board - still shows through, and a keyed blit stays close to a
            # plain memcpy where per-pixel alpha would not.
            self._board.set_colorkey(self.theme.background)
        board = self._board
        board.fill(self.theme.background)
        self._paint_walls(board, maze)
        self._paint_pellets(board, world)
        return board

    def _paint_walls(self, board, maze: Maze) -> None:
        """Filled wall tiles plus an outline on every edge facing a corridor.

        Drawing the outline only where a wall touches a non-wall is what gives
        the classic "double line" arcade maze: interior wall-to-wall seams stay
        invisible, so a 2x2 block of walls reads as one rounded slab.
        """
        self._ensure_wall_geometry(maze)
        theme = self.theme
        ox, oy = self.layout.maze_origin
        for rect in self._wall_rects:
            board.fill(theme.wall, rect.move(-ox, -oy))
        thickness = max(1, self.layout.tile // 12)
        for start, end in self._wall_edges:
            pygame.draw.line(
                board, theme.wall_edge,
                (start[0] - ox, start[1] - oy), (end[0] - ox, end[1] - oy),
                thickness,
            )

        # The ghost-house door: a thin bar, not a full tile, so it reads as a
        # gate rather than a wall you could walk on.
        tile_px = self.layout.tile
        bar_h = max(2, tile_px // 6)
        for tile in maze.door_tiles:
            x, y = self.layout.tile_px(tile)
            board.fill(
                theme.door,
                pygame.Rect(x - ox, y - oy + (tile_px - bar_h) // 2, tile_px, bar_h),
            )

    def _paint_pellets(self, board, world: GameWorld) -> None:
        """The ordinary pellets, painted into the cached board."""
        theme = self.theme
        ox, oy = self.layout.maze_origin
        radius = max(1, int(self.layout.tile * 0.10))
        for tile in world.pellets:
            cx, cy = self.layout.centre_px(tile)
            pygame.draw.circle(board, theme.pellet, (cx - ox, cy - oy), radius)

    def _ensure_wall_geometry(self, maze: Maze) -> None:
        """(Re)build the cached wall rects and outline segments for *maze*."""
        if self._wall_cache_key is maze:
            return
        self._wall_cache_key = maze

        tile_px = self.layout.tile
        rects: List[pygame.Rect] = []
        edges: List[Tuple[Tuple[int, int], Tuple[int, int]]] = []

        def is_wall(tile: Tile) -> bool:
            return maze.char_at(tile) == WALL_CHAR

        for y in range(maze.height):
            for x in range(maze.width):
                if not is_wall((x, y)):
                    continue
                left, top = self.layout.tile_px((x, y))
                right, bottom = left + tile_px, top + tile_px
                rects.append(pygame.Rect(left, top, tile_px, tile_px))
                # An edge is drawn only where this wall meets open space; the
                # outside of the map counts as open so the border gets outlined.
                if not is_wall((x, y - 1)):
                    edges.append(((left, top), (right, top)))
                if not is_wall((x, y + 1)):
                    edges.append(((left, bottom - 1), (right, bottom - 1)))
                if not is_wall((x - 1, y)):
                    edges.append(((left, top), (left, bottom)))
                if not is_wall((x + 1, y)):
                    edges.append(((right - 1, top), (right - 1, bottom)))

        self._wall_rects = rects
        self._wall_edges = edges

    def _draw_power_pellets(self, surface, world: GameWorld) -> None:
        """The four power pellets, pulsing.

        Kept out of the cached board precisely because they move: they are the
        highest-value thing on the screen, and a pulse is what makes the eye
        find them.  Four circles a frame is nothing.
        """
        theme = self.theme
        # Pure function of elapsed time: no animation counter to fall out of
        # step with a paused or restarted world.
        phase = 0.5 + 0.5 * math.sin(world.elapsed * PELLET_BLINK_HZ * math.tau)
        big = int(self.layout.tile * (0.24 + 0.10 * phase))
        colour = blend(theme.power_pellet, theme.background, 0.35 * (1.0 - phase))
        for tile in world.power_pellets:
            pygame.draw.circle(surface, colour, self._px(tile), max(2, big))

    # ── Pac-Man ───────────────────────────────────────────────────────────────

    def _draw_pacman(self, surface, world: GameWorld) -> None:
        pac: Pacman = world.pacman
        centre = self._px(pac.position)
        radius = max(3, int(self.layout.tile * 0.46))

        if world.phase is GamePhase.DYING:
            # Death animation: the mouth opens all the way round until he
            # vanishes.  phase_timer runs down, so 1 - t is "how far through".
            span = max(0.001, world.rules.dying_duration)
            progress = 1.0 - max(0.0, min(1.0, world.phase_timer / span))
            gape = 20.0 + 320.0 * progress
            self._draw_pac_polygon(surface, centre, radius, self._facing(pac), gape)
            return

        # A pure function of elapsed time: no per-renderer animation counter to
        # get out of step with a paused or rewound world.
        chomp = abs(math.sin(world.elapsed * CHOMP_HZ * math.pi))
        gape = CHOMP_ANGLE * chomp
        self._draw_pac_polygon(surface, centre, radius, self._facing(pac), gape)

    def _draw_pac_polygon(
        self, surface, centre: Tuple[int, int], radius: int, facing: float, gape: float
    ) -> None:
        """A wedge-mouthed disc pointing along *facing* (degrees, CCW)."""
        if gape >= 358.0:
            return
        half = math.radians(gape) / 2.0
        base = math.radians(facing)
        cx, cy = centre
        points = [(cx, cy)]
        steps = 26
        start, end = base + half, base + math.tau - half
        for i in range(steps + 1):
            angle = start + (end - start) * i / steps
            # Screen y grows downward, hence the minus on the sine.
            points.append((cx + radius * math.cos(angle), cy - radius * math.sin(angle)))
        pygame.draw.polygon(surface, self.theme.pacman, points)

    # ── Ghosts ────────────────────────────────────────────────────────────────

    def _draw_ghost(self, surface, ghost: Ghost, world: GameWorld) -> None:
        """Dome + torso + wavy skirt, or eyes only once the ghost is eaten."""
        theme = self.theme
        centre = self._px(ghost.position)
        radius = max(3, int(self.layout.tile * 0.44))
        facing = ghost.direction if ghost.direction.is_moving else ghost.last_direction

        if ghost.state is not GhostState.EATEN:
            if ghost.state is GhostState.FRIGHTENED:
                body = theme.frightened_blink if ghost.blinking else theme.frightened
            else:
                body = theme.agent_colour(ghost.name)
            self._draw_ghost_body(surface, centre, radius, body)

        eye_colour = theme.ghost_eye
        pupil = theme.ghost_pupil
        if ghost.state is GhostState.FRIGHTENED and not ghost.blinking:
            # Keep the pupils readable against the deep-blue frightened body.
            pupil = theme.frightened_blink
        self._draw_ghost_eyes(surface, centre, radius, facing, eye_colour, pupil)

    def _draw_ghost_body(
        self, surface, centre: Tuple[int, int], radius: int, colour: Colour
    ) -> None:
        cx, cy = centre
        dome_y = cy - int(radius * 0.20)
        skirt_y = cy + int(radius * 0.55)
        bottom = cy + radius

        pygame.draw.circle(surface, colour, (cx, dome_y), radius)
        surface.fill(
            colour, pygame.Rect(cx - radius, dome_y, radius * 2, max(1, skirt_y - dome_y))
        )

        # Three downward humps.  Triangles rather than arcs: at 24 px a tile the
        # difference is invisible and a polygon is one call instead of three.
        humps = 3
        width = (radius * 2) / humps
        for i in range(humps):
            x0 = cx - radius + width * i
            pygame.draw.polygon(
                surface, colour,
                [(x0, skirt_y), (x0 + width / 2.0, bottom), (x0 + width, skirt_y)],
            )

    def _draw_ghost_eyes(
        self,
        surface,
        centre: Tuple[int, int],
        radius: int,
        facing: Direction,
        eye: Colour,
        pupil: Colour,
    ) -> None:
        cx, cy = centre
        eye_r = max(2, int(radius * 0.32))
        pupil_r = max(1, int(eye_r * 0.52))
        offset_x = int(radius * 0.42)
        eye_y = cy - int(radius * 0.18)

        dx, dy = facing.delta
        look = int(eye_r - pupil_r)
        for sign in (-1, 1):
            ex = cx + sign * offset_x
            pygame.draw.circle(surface, eye, (ex, eye_y), eye_r)
            pygame.draw.circle(surface, pupil, (ex + dx * look, eye_y + dy * look), pupil_r)

    def _facing(self, agent: Agent) -> float:
        """Screen angle for an agent, in degrees.

        ``direction`` is ``NONE`` at every tile centre (the decision-point
        contract), so falling back to ``last_direction`` is what stops Pac-Man
        snapping east once per tile.
        """
        direction = agent.direction if agent.direction.is_moving else agent.last_direction
        return _FACING.get(direction, 0.0)

    # ── Debug overlay ─────────────────────────────────────────────────────────

    def _draw_debug(self, surface, world: GameWorld) -> None:
        """Every agent's current target, plus a label on the tunnel row(s).

        Pac-Man is in the loop too: with ``--auto`` his tree aims at tiles just
        like a ghost's, and seeing the pellet he picked is the fastest way to
        tell a bad target apart from a bad step.
        """
        theme = self.theme
        marker = max(3, self.layout.tile // 3)
        for ghost in world.agents:
            if ghost.target is None:
                continue
            colour = theme.agent_colour(ghost.name)
            # Faint = blended toward the background rather than alpha-blitted:
            # no per-frame surface allocation, same visual result on black.
            faint = blend(colour, theme.background, 0.6)
            start = self._px(ghost.position)
            end = self._px(ghost.target)
            pygame.draw.line(surface, faint, start, end, 1)
            rect = pygame.Rect(0, 0, marker, marker)
            rect.center = end
            pygame.draw.rect(surface, colour, rect, 1)

        font = self.hud.small_font
        faint_accent = blend(theme.accent, theme.background, 0.4)
        for row in world.maze.tunnel_rows:
            x, y = self._tpx((0, row))
            label = font.render(f"tunnel r{row}", True, faint_accent)
            pad = max(0, (self.layout.tile - label.get_height()) // 2)
            surface.blit(label, (x + 2, y + pad))

    # ── Centred overlay text ──────────────────────────────────────────────────

    def _draw_overlay(self, surface, text: str) -> None:
        """A centred card over the maze: READY!, PAUSED, GAME OVER, the title.

        *text* is split on newlines.  The first line is the headline; every
        later line is a smaller caption underneath it, and a blank line is a
        gap.  One string keeps the ``overlay=`` contract between app and
        renderer exactly as it was.
        """
        theme = self.theme
        lines = text.split("\n")
        tile = self.layout.tile
        small = self.hud.fonts.at(max(12, int(tile * 0.75)))

        rows = [(self.overlay_font.render(lines[0], True, theme.pacman), 0)]
        for line in lines[1:]:
            if not line:
                rows.append((None, tile // 2))
                continue
            rows.append((small.render(line, True, theme.text_bright), tile // 3))

        width = max(r.get_width() for r, _ in rows if r is not None)
        height = sum((r.get_height() if r is not None else 0) + gap for r, gap in rows)

        maze_rect = pygame.Rect(
            self.layout.maze_origin[0], self.layout.maze_origin[1],
            self.layout.maze_w, self.layout.maze_h,
        )
        card = pygame.Rect(0, 0, width + tile * 3, height + tile * 2)
        card.center = maze_rect.center

        # Dim the whole maze a little, then the card on top: the board behind
        # is busy, and bare text over pellets is genuinely hard to read.
        scrim = pygame.Surface(maze_rect.size, pygame.SRCALPHA)
        scrim.fill((*theme.background, 120))
        surface.blit(scrim, maze_rect.topleft)

        backdrop = pygame.Surface(card.size, pygame.SRCALPHA)
        pygame.draw.rect(backdrop, (*theme.panel_bg, 238), backdrop.get_rect(),
                         border_radius=tile // 2)
        surface.blit(backdrop, card.topleft)
        pygame.draw.rect(surface, theme.accent, card, 2, border_radius=tile // 2)

        y = card.top + tile
        for rendered, gap in rows:
            y += gap
            if rendered is not None:
                surface.blit(rendered, rendered.get_rect(midtop=(card.centerx, y)))
                y += rendered.get_height()

    def __repr__(self) -> str:  # noqa: D105
        mode = "headless" if self.headless else "windowed"
        return f"Renderer({mode}, {self.layout.width}x{self.layout.height})"
