"""Effects — the DELUXE overlay: neon bloom, particles, popups and a CRT pass.

This module is the whole difference between ``VisualStyle.LITE`` and
``VisualStyle.DELUXE``.  It is deliberately a *separate object the renderer
drives* rather than a set of branches inside :mod:`renderer`: LITE simply never
constructs an :class:`EffectLayer`, so the calm preset pays literally nothing —
not a branch, not an allocation, not an import of anything extra.

Three rules shaped every line below.

* **Nothing is allocated per frame.**  A pygame renderer dies of ``Surface``
  churn long before it dies of blits, so every pixel this layer paints comes
  from a surface built once in :meth:`__init__` or memoised in a small keyed
  cache.  The hot path is blits and float arithmetic only.
* **Additive glow, not alpha glow.**  Bloom is blitted with
  ``BLEND_RGB_ADD`` onto plain RGB sprites whose colour *is* their intensity
  (black adds nothing).  That skips the alpha channel entirely, which is both
  faster and the physically right model for light on a dark board.
* **Two animated things at a time, easing out.**  Everything decelerates as it
  dies, and the CRT pass stays subtle enough that the ghosts are still the
  clearest objects on screen.  Effects that fight the gameplay are noise.

The CRT treatment covers the *maze viewport only*.  Scanlines over the
behaviour-tree panel would cost legibility on the one region of the window that
is pure text, and the panel is the point of the project.

**Shake contract.**  :meth:`shake` reports the current offset; this layer
already applies it to its own world-space effects (bloom, particles, popups)
and deliberately does *not* apply it to the CRT pass, which represents the
glass rather than the world.  The renderer should therefore offset the maze and
the sprites by :meth:`shake` and leave this layer alone.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Dict, List, Sequence, Tuple

import pygame

from pacman_bteng.gui.layout import Layout
from pacman_bteng.gui.theme import Colour, Theme

if TYPE_CHECKING:  # pragma: no cover - typing only, keeps the import graph thin
    from pacman_bteng.game.world import GameEvent, GameWorld

#: Only ``#`` is a drawn wall; ``X`` is void.  Must agree with the renderer or
#: the bloom would halo the black nothing outside the tunnel mouths.
WALL_CHAR = "#"

#: Hard ceiling on live particles.  A burst-heavy frame (power pellet plus a
#: ghost eaten) spawns ~80, so this is headroom rather than a limit that bites,
#: and it is what stops a pathological run from turning into a slideshow.
MAX_PARTICLES = 320

#: Ceiling on floating score popups.  More than a handful on screen at once is
#: unreadable anyway.
MAX_POPUPS = 12

#: Power pellets pulse at this rate.  Slower than the LITE blink: at DELUXE the
#: glow radius carries the signal, so the pulse can breathe instead of flicker.
POWER_PULSE_HZ = 1.1

#: The pulse is quantised into this many steps so the glow sprite comes from
#: the cache instead of being rebuilt every frame.  Eight is past the point
#: where the eye can see the stepping at 60 fps.
PULSE_STEPS = 8

#: Particle brightness is quantised the same way, for the same reason.
FADE_STEPS = 5

#: Screen shake: starting amplitude in tiles, and how fast it decays.  Small and
#: quick — a shake you have time to read is a shake that hurts the player.
SHAKE_TILES = 0.32
SHAKE_DECAY = 7.5
SHAKE_HZ_X = 27.0
SHAKE_HZ_Y = 19.0


def _ease_out(t: float) -> float:
    """Cubic ease-out on ``[0, 1]`` — fast then settling, never linear."""
    t = 0.0 if t < 0.0 else 1.0 if t > 1.0 else t
    inv = 1.0 - t
    return 1.0 - inv * inv * inv


def _scale(colour: Colour, factor: float) -> Colour:
    """Multiply a colour toward black; the additive blitter reads that as
    brightness, so this is the only "alpha" the glow sprites need."""
    factor = 0.0 if factor < 0.0 else factor
    return (
        min(255, int(colour[0] * factor)),
        min(255, int(colour[1] * factor)),
        min(255, int(colour[2] * factor)),
    )


# ── Live effect records ───────────────────────────────────────────────────────
# Plain slotted dataclasses holding float pixel coordinates.  Emphatically *not*
# one Surface per particle: 300 tiny surfaces would be 300 allocations a burst.


@dataclass(slots=True)
class _Particle:
    x: float
    y: float
    vx: float
    vy: float
    life: float
    ttl: float
    colour: Colour
    radius: int


@dataclass(slots=True)
class _Popup:
    x: float
    y: float
    text: str
    colour: Colour
    life: float
    ttl: float


class EffectLayer:
    """The deluxe overlay.  Construct once per window; drive it per frame.

    The renderer's frame becomes::

        fx.feed(world, events)
        fx.update(dt)
        fx.draw_under(screen, world)     # before maze and sprites
        ... existing maze / sprite drawing, offset by fx.shake() ...
        fx.draw_over(screen, world)      # after
    """

    def __init__(self, layout: Layout, theme: Theme) -> None:
        self.layout = layout
        self.theme = theme

        tile = max(1, layout.tile)
        self._strength = float(getattr(theme, "glow_strength", 1.0))
        self._crt_on = bool(getattr(theme, "scanlines", True))

        # ── Colours, resolved once ────────────────────────────────────────────
        # getattr with a fallback throughout: this file has to keep working
        # against a Theme that predates the refreshed field list.
        self._wall_glow: Colour = getattr(theme, "wall_glow", theme.wall_edge)
        self._grid_line: Colour = getattr(theme, "grid_line", (14, 20, 44))
        self._accent_dim: Colour = getattr(theme, "accent_dim", theme.accent)
        self._success: Colour = getattr(theme, "success", (94, 224, 130))
        self._danger: Colour = getattr(theme, "danger", (255, 92, 92))

        # ── Live state ────────────────────────────────────────────────────────
        self._particles: List[_Particle] = []
        self._popups: List[_Popup] = []
        self._shake_amp = 0.0
        self._clock = 0.0
        self._rng = random.Random(0xC0FFEE)   # deterministic: screenshots diff

        # ── Caches (every Surface this module will ever own) ───────────────────
        self._glow_cache: Dict[Tuple[Colour, int], pygame.Surface] = {}
        self._text_cache: Dict[Tuple[str, Colour], pygame.Surface] = {}
        self._font = pygame.font.Font(None, max(13, int(tile * 0.92)))

        # Bloom radii, derived from the tile so a shrunk window stays coherent.
        self._pellet_r = max(2, int(tile * 0.42))
        self._power_r = max(3, int(tile * 0.80))
        self._sprite_r = max(4, int(tile * 1.05))

        # The static pass (wall halo + grid) never changes while the maze does
        # not, so it is one blit per frame instead of ~1100 draw calls.
        self._static_key: Any = None
        self._static = pygame.Surface((layout.maze_w, layout.maze_h))
        self._crt = self._build_crt()

    # ── Events in ─────────────────────────────────────────────────────────────

    def feed(self, world: "GameWorld", events: Sequence["GameEvent"]) -> None:
        """React to *this frame's* events.

        Reading the world too (rather than events alone) is what lets an event
        without a tile — ``extra_life`` carries none — still land somewhere
        meaningful instead of at the origin.
        """
        if not events:
            return
        theme = self.theme
        for event in events:
            kind = getattr(event, "kind", "")
            tile = getattr(event, "tile", None)
            points = int(getattr(event, "points", 0) or 0)

            if kind == "pellet":
                # Deliberately tiny: this fires up to eight times a second and
                # is meant to read as a crumb, not as a firework.
                self._burst(tile, theme.pellet, count=4, speed=52.0, ttl=0.30)
            elif kind == "power":
                self._burst(tile, getattr(theme, "power_pellet", theme.pellet),
                            count=26, speed=170.0, ttl=0.75)
                self._burst(tile, self._accent_dim, count=10, speed=95.0, ttl=0.95)
            elif kind == "ghost_eaten":
                colour = self._event_ghost_colour(event)
                self._burst(tile, colour, count=30, speed=190.0, ttl=0.70)
                if points:
                    self._popup(tile, f"+{points}", colour)
            elif kind == "ghost_home":
                self._burst(tile, self._event_ghost_colour(event),
                            count=8, speed=70.0, ttl=0.45)
            elif kind == "pacman_caught":
                where = tile or getattr(world.pacman, "tile", None)
                self._burst(where, theme.pacman, count=34, speed=210.0, ttl=0.85)
                self._burst(where, self._danger, count=14, speed=120.0, ttl=1.00)
                self._shake_amp = SHAKE_TILES * self.layout.tile
            elif kind == "extra_life":
                where = tile or getattr(world.pacman, "tile", None)
                self._popup(where, "1UP", self._success, ttl=1.4)
                self._burst(where, self._success, count=18, speed=130.0, ttl=0.80)
            elif kind == "level_clear":
                where = tile or getattr(world.pacman, "tile", None)
                self._burst(where, self._accent_dim, count=28, speed=175.0, ttl=1.05)

    # ── Time ──────────────────────────────────────────────────────────────────

    def update(self, dt: float) -> None:
        """Advance every live effect and drop the dead ones."""
        if dt <= 0.0:
            return
        self._clock += dt

        # Drag rather than a fixed decay: a particle that decelerates reads as
        # a spark thrown into air, and it is the ease-out the design asks for.
        drag = max(0.0, 1.0 - 3.1 * dt)
        alive: List[_Particle] = []
        for p in self._particles:
            p.life -= dt
            if p.life <= 0.0:
                continue
            p.x += p.vx * dt
            p.y += p.vy * dt
            p.vx *= drag
            p.vy = p.vy * drag + 60.0 * dt    # a whisper of gravity, for weight
            alive.append(p)
        self._particles = alive

        live_popups: List[_Popup] = []
        for popup in self._popups:
            popup.life -= dt
            if popup.life > 0.0:
                live_popups.append(popup)
        self._popups = live_popups

        if self._shake_amp > 0.0:
            self._shake_amp *= math.exp(-SHAKE_DECAY * dt)
            if self._shake_amp < 0.35:
                self._shake_amp = 0.0

    def shake(self) -> Tuple[int, int]:
        """Current screen-shake offset in pixels; ``(0, 0)`` when calm."""
        amp = self._shake_amp
        if amp <= 0.0:
            return (0, 0)
        t = self._clock
        return (
            int(round(math.sin(t * SHAKE_HZ_X) * amp)),
            int(round(math.sin(t * SHAKE_HZ_Y + 1.3) * amp * 0.75)),
        )

    def reset(self) -> None:
        """Drop every live effect — called on restart, so a new run starts calm.

        The caches survive on purpose: they describe the *window*, not the run,
        and rebuilding them would be the one big allocation this module avoids.
        """
        self._particles.clear()
        self._popups.clear()
        self._shake_amp = 0.0

    @property
    def live(self) -> int:
        """How many effects are currently animating.  Exists for the tests."""
        return len(self._particles) + len(self._popups) + (1 if self._shake_amp else 0)

    # ── Painted before the maze ───────────────────────────────────────────────

    def draw_under(self, surface: pygame.Surface, world: "GameWorld") -> None:
        """Backdrop grid, wall halo and every object's bloom.

        Under, not over: bloom that sits *behind* the maze reads as light
        leaking out from the geometry.  Painted on top it would fog the walls
        and cost the pellets their contrast.
        """
        if self._strength <= 0.0:
            return
        self._ensure_static(world.maze)
        ox, oy = self.layout.maze_origin
        sx, sy = self.shake()
        surface.blit(self._static, (ox + sx, oy + sy), special_flags=pygame.BLEND_RGB_ADD)

        centre = self.layout.centre_px
        add = pygame.BLEND_RGB_ADD

        # Pellets: ~240 blits of a 9x9 sprite is far cheaper than one blit of a
        # maze-sized bloom surface, and it needs no invalidation when one is
        # eaten.  Dim, because 240 of anything is a lot of light.
        halo = self._glow(_scale(self.theme.pellet, 0.30 * self._strength), self._pellet_r)
        half = halo.get_width() // 2
        for tile in world.pellets:
            px, py = centre(tile)
            surface.blit(halo, (px - half + sx, py - half + sy), special_flags=add)

        # Power pellets pulse.  They are the highest-value thing on the board,
        # so they get the one genuinely animated bloom.
        pulse = 0.5 + 0.5 * math.sin(world.elapsed * POWER_PULSE_HZ * math.tau)
        step = int(pulse * (PULSE_STEPS - 1) + 0.5)
        frac = step / (PULSE_STEPS - 1)
        power_colour = getattr(self.theme, "power_pellet", self.theme.pellet)
        big = self._glow(
            _scale(power_colour, (0.55 + 0.45 * frac) * self._strength),
            self._power_r + int(self._power_r * 0.35 * frac),
        )
        half = big.get_width() // 2
        for tile in world.power_pellets:
            px, py = centre(tile)
            surface.blit(big, (px - half + sx, py - half + sy), special_flags=add)

        # Sprites.  A ghost is the thing the player is tracking; giving it a
        # halo in its own colour is the cheapest possible readability win.
        for ghost in world.ghosts:
            colour = self._ghost_glow_colour(ghost)
            if colour is None:
                continue
            self._blit_glow(surface, ghost.position, colour, self._sprite_r, sx, sy)
        self._blit_glow(surface, world.pacman.position, self.theme.pacman,
                        self._sprite_r, sx, sy)

    # ── Painted after the maze ────────────────────────────────────────────────

    def draw_over(self, surface: pygame.Surface, world: "GameWorld") -> None:
        """Particles, score popups, then the CRT glass on top of everything."""
        add = pygame.BLEND_RGB_ADD
        sx, sy = self.shake()

        # A zero-strength palette makes every spark black, so the whole loop
        # would be blits of nothing.  One branch out here, none inside.
        for p in (self._particles if self._strength > 0.0 else ()):
            k = p.life / p.ttl if p.ttl > 0.0 else 0.0
            # Quantised so the sprite comes out of the cache; the eye reads the
            # shrink-and-dim as a continuous fade regardless.
            level = int(_ease_out(k) * (FADE_STEPS - 1) + 0.5)
            if level <= 0:
                continue
            fade = level / (FADE_STEPS - 1)
            radius = max(1, int(p.radius * (0.45 + 0.55 * fade)))
            sprite = self._glow(_scale(p.colour, fade * fade * self._strength), radius)
            half = sprite.get_width() // 2
            surface.blit(sprite, (int(p.x) - half + sx, int(p.y) - half + sy),
                         special_flags=add)

        for popup in self._popups:
            k = 1.0 - (popup.life / popup.ttl if popup.ttl > 0.0 else 0.0)
            label = self._text(popup.text, popup.colour)
            # Rises fast and then hangs: ease-out on the travel, a late fade on
            # the alpha, so the number is readable for most of its life.
            rise = _ease_out(k) * self.layout.tile * 1.9
            alpha = 255 if k < 0.55 else int(255 * (1.0 - (k - 0.55) / 0.45))
            label.set_alpha(max(0, min(255, alpha)))
            surface.blit(label, (int(popup.x - label.get_width() / 2) + sx,
                                 int(popup.y - rise) + sy))

        if self._crt is not None:
            ox, oy = self.layout.maze_origin
            surface.blit(self._crt, (ox, oy))

    # ── Spawning ──────────────────────────────────────────────────────────────

    def _burst(self, tile, colour: Colour, *, count: int, speed: float,
               ttl: float) -> None:
        """Radial spray of particles from the centre of *tile*."""
        if tile is None:
            return
        px, py = self.layout.centre_px(tile)
        rng = self._rng
        base = rng.random() * math.tau
        radius = max(2, int(self.layout.tile * 0.30))
        for i in range(count):
            # Even angular spacing plus jitter: a purely random spray clumps,
            # and a clumped burst looks like a bug rather than an explosion.
            angle = base + math.tau * i / count + rng.uniform(-0.25, 0.25)
            v = speed * rng.uniform(0.35, 1.0)
            self._particles.append(
                _Particle(
                    x=float(px), y=float(py),
                    vx=math.cos(angle) * v, vy=-math.sin(angle) * v,
                    life=ttl * rng.uniform(0.65, 1.0), ttl=ttl,
                    colour=colour, radius=radius,
                )
            )
        overflow = len(self._particles) - MAX_PARTICLES
        if overflow > 0:
            del self._particles[:overflow]      # oldest first: they are dimmest

    def _popup(self, tile, text: str, colour: Colour, ttl: float = 1.05) -> None:
        if tile is None:
            return
        px, py = self.layout.centre_px(tile)
        self._popups.append(_Popup(float(px), float(py), text, colour, ttl, ttl))
        overflow = len(self._popups) - MAX_POPUPS
        if overflow > 0:
            del self._popups[:overflow]

    def _event_ghost_colour(self, event: "GameEvent") -> Colour:
        """Ghost colour for a ``ghost_eaten`` / ``ghost_home`` event.

        The event carries no ghost reference, only a message that starts with
        the ghost's name in upper case, so the name is parsed back out.  An
        unrecognised word degrades to Pac-Man yellow via ``agent_colour``.
        """
        message = str(getattr(event, "message", "") or "")
        name = message.split(" ", 1)[0].lower() if message else ""
        return self.theme.agent_colour(name)

    def _ghost_glow_colour(self, ghost) -> Colour | None:
        """Bloom colour for a ghost, or ``None`` when it is eyes only.

        Frightened ghosts glow in the frightened colour because that, not the
        body shape, is the signal the player acts on; an eaten ghost is a pair
        of eyes crossing the board and giving it a halo would overstate it.
        """
        state = getattr(ghost.state, "value", "")
        if state == "eaten":
            return None
        if state == "frightened":
            return (self.theme.frightened_blink if ghost.blinking
                    else getattr(self.theme, "accent_dim", self.theme.frightened))
        return self.theme.agent_colour(ghost.name)

    def _blit_glow(self, surface, position, colour: Colour, radius: int,
                   sx: int, sy: int) -> None:
        sprite = self._glow(_scale(colour, 0.62 * self._strength), radius)
        px, py = self.layout.centre_px(position)
        half = sprite.get_width() // 2
        surface.blit(sprite, (px - half + sx, py - half + sy),
                     special_flags=pygame.BLEND_RGB_ADD)

    # ── Cached surfaces ───────────────────────────────────────────────────────

    def _glow(self, colour: Colour, radius: int) -> pygame.Surface:
        """A radial-gradient sprite, memoised by ``(colour, radius)``.

        Plain RGB, no alpha: it is only ever blitted with ``BLEND_RGB_ADD``, so
        black is transparent for free and the blitter has one less channel to
        touch.  Callers pre-scale the colour, which is why the cache key is the
        *scaled* colour — quantised brightness levels keep the key space small
        (a few dozen entries for a whole session).
        """
        key = (colour, radius)
        cached = self._glow_cache.get(key)
        if cached is not None:
            return cached

        size = radius * 2 + 1
        surf = pygame.Surface((size, size))
        surf.fill((0, 0, 0))
        # Outermost ring first so each smaller, brighter circle overwrites the
        # dimmer one underneath: a gradient out of flat draw calls.
        for r in range(radius, 0, -1):
            t = 1.0 - (r - 1) / radius
            pygame.draw.circle(surf, _scale(colour, t * t), (radius, radius), r)
        try:
            surf = surf.convert()
        except pygame.error:  # pragma: no cover - no display yet; still usable
            pass
        self._glow_cache[key] = surf
        return surf

    def _text(self, text: str, colour: Colour) -> pygame.Surface:
        """Memoised rendered text.  Popup strings repeat (``+200``, ``+400``,
        ``1UP``), so the cache stays tiny and no frame ever calls ``render``."""
        key = (text, colour)
        cached = self._text_cache.get(key)
        if cached is None:
            cached = self._font.render(text, True, colour).convert_alpha()
            self._text_cache[key] = cached
        return cached

    def _ensure_static(self, maze) -> None:
        """(Re)build the wall halo and backdrop grid for *maze*.

        Both are pure functions of the maze, so this runs once per maze object
        and the frame loop gets a single additive blit out of it.  The build
        costs ~10 ms, which is one dropped frame the first time the board is
        drawn — during the READY countdown, where nothing is moving yet.
        """
        if self._static_key is maze:
            return
        self._static_key = maze

        tile = self.layout.tile
        surf = self._static
        surf.fill((0, 0, 0))

        # The grid first, underneath everything.  Deliberately near-invisible:
        # at a one-tile pitch every line lands on a wall edge in the corridors,
        # so what actually shows is the open void beyond the tunnels — and there
        # it wants to read as faint texture, not as a lattice competing with
        # the pellets.
        grid = _scale(self._grid_line, 0.4 * self._strength)
        if max(grid) > 0:
            for x in range(0, self.layout.maze_w + 1, tile):
                pygame.draw.line(surf, grid, (x, 0), (x, self.layout.maze_h), 1)
            for y in range(0, self.layout.maze_h + 1, tile):
                pygame.draw.line(surf, grid, (0, y), (self.layout.maze_w, y), 1)

        # Wall halo: every edge wall tile gets three inflated rects, dimmest
        # first, so the light escaping past the wall fill falls off with
        # distance.  Interior wall tiles are fully enclosed and contribute
        # nothing visible, so skipping them roughly halves the work.
        char_at = maze.char_at
        edge: List[Tuple[int, int]] = []
        for y in range(maze.height):
            for x in range(maze.width):
                if char_at((x, y)) != WALL_CHAR:
                    continue
                if (char_at((x, y - 1)) == WALL_CHAR and char_at((x, y + 1)) == WALL_CHAR
                        and char_at((x - 1, y)) == WALL_CHAR
                        and char_at((x + 1, y)) == WALL_CHAR):
                    continue
                edge.append((x * tile, y * tile))

        # Ring-major, not tile-major.  Painting tile by tile lets a neighbour's
        # dim outer ring overwrite this tile's bright inner one, which shows up
        # as a scalloped edge along every wall run; doing a whole ring at a time
        # keeps the falloff monotonic.  Square rects for the same reason -
        # rounded corners on a per-tile rect scallop wall runs on their own.
        # No ring at grow == 0: the renderer paints the tile opaque over it.
        rings = (
            (max(4, int(tile * 0.70)), 0.08),
            (max(3, int(tile * 0.46)), 0.17),
            (max(2, int(tile * 0.27)), 0.29),
            (max(1, int(tile * 0.11)), 0.46),
        )
        for grow, level in rings:
            colour = _scale(self._wall_glow, level * self._strength)
            if max(colour) <= 0:
                continue
            span = tile + grow * 2
            for left, top in edge:
                pygame.draw.rect(surf, colour,
                                 pygame.Rect(left - grow, top - grow, span, span))
        try:
            self._static = surf.convert()
        except pygame.error:  # pragma: no cover - no display yet; still usable
            self._static = surf

    def _build_crt(self):
        """The CRT glass: a vignette band plus scanlines, in one cached surface.

        One per-pixel-alpha blit a frame, over the maze only.  Building it as a
        single surface (rather than a vignette surface and a scanline surface)
        halves the most expensive blit in the layer, and the scanlines stop at
        the inset so the darkest border stays clean — which is what a real tube
        does anyway.
        """
        if not self._crt_on:
            return None
        w, h = self.layout.maze_w, self.layout.maze_h
        if w <= 0 or h <= 0:
            return None

        surf = pygame.Surface((w, h), pygame.SRCALPHA)
        surf.fill((0, 0, 0, 0))

        # Vignette: concentric 1 px frames, alpha rising toward the edge.  They
        # never overlap, so writing alpha directly composes correctly.
        depth = max(4, int(self.layout.tile * 2.6))
        peak = 108
        for i in range(depth):
            k = 1.0 - i / depth
            alpha = int(peak * k * k)
            if alpha <= 0:
                continue
            pygame.draw.rect(surf, (0, 0, 0, alpha),
                             pygame.Rect(i, i, w - 2 * i, h - 2 * i), 1)

        # Scanlines: every third row, inside the vignette band.  Subtle on
        # purpose — a heavy comb would eat the pellets, which are 2 px wide.
        inset = depth
        line = (0, 0, 0, 44)
        for y in range(inset, h - inset, 3):
            pygame.draw.line(surf, line, (inset, y), (w - inset, y), 1)

        try:
            return surf.convert_alpha()
        except pygame.error:  # pragma: no cover - no display yet; still usable
            return surf

    def __repr__(self) -> str:  # noqa: D105
        return (
            f"EffectLayer({self.layout.maze_w}x{self.layout.maze_h}, "
            f"live={self.live}, glow_sprites={len(self._glow_cache)})"
        )
