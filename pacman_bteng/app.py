"""PacmanApp — the frame loop that ties the four layers together.

One frame, in order:

    1. drain the keyboard              gui.InputHandler
    2. tick every behavior tree        behaviors.BrainPool   (ghosts decide)
    3. advance the world               game.GameWorld        (everyone moves)
    4. paint                           gui.Renderer

Step 2 sits *before* step 3 on purpose: a tree decides which tile to head for,
and the world then carries that decision out.  Reversing them would make every
ghost act on a frame-old view of the maze, which is exactly the kind of one-
frame lag that turns "the ghost cut me off" into "the ghost walked through me".

The trees are ticked only while the world is actually running (phase PLAYING,
not paused).  A ghost frozen during READY or during the death animation should
not be quietly committing to its next three tiles.
"""

from __future__ import annotations

import sys
import time
from typing import List, Optional

from pacman_bteng.behaviors import BrainError, BrainPool
from pacman_bteng.config import Settings
from pacman_bteng.game import GamePhase, Maze, GameWorld
from pacman_bteng.game.world import GameEvent
from pacman_bteng.game.geometry import Direction
from pacman_bteng.introspection import BTIntrospection

#: Longest frame the world is allowed to see.  A dragged window or a breakpoint
#: produces a multi-second dt, and integrating that in one step teleports
#: everyone through walls.  Better a brief slow-motion than a broken board.
MAX_FRAME_DT = 0.05

#: How long GAME OVER stays on screen before ``--loop`` deals a new game.
GAME_OVER_LINGER = 4.0

#: What to write across the maze in each non-playing phase.
_OVERLAYS = {
    GamePhase.READY: "READY!",
    GamePhase.DYING: "OUCH!",
    GamePhase.LEVEL_CLEAR: "LEVEL CLEARED",
    GamePhase.GAME_OVER: "GAME OVER   -   press R",
}


class PacmanApp:
    """A single run of the game, GUI included."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.maze = Maze.from_file(settings.maze_file)
        self.world = GameWorld(
            self.maze,
            settings.rules,
            ghost_names=settings.ghosts,
            level=settings.level,
            seed=settings.seed,
        )

        self.introspection = BTIntrospection(settings.introspection)
        self.brains = BrainPool(self.world, settings, introspection=self.introspection)

        self.debug = settings.debug
        self.show_panel = settings.window.show_panel
        self.autopilot = settings.autopilot
        self.running = True

        # Imported late so the game logic and the behavior trees stay usable
        # (and testable) on a machine with no display and no pygame at all.
        from pacman_bteng.gui import InputHandler, Layout, Renderer, Theme

        self.style = settings.window.visual_style()
        self.layout = Layout.compute(
            self.maze,
            settings.window.tile_size,
            show_panel=self.show_panel,
            max_size=_screen_limit(settings),
        )
        self.renderer = Renderer(
            self.layout,
            Theme.for_style(self.style),
            show_panel=self.show_panel,
            caption=settings.window.caption,
            headless=settings.headless,
            style=self.style,
        )
        self.input = InputHandler()

        self._elapsed = 0.0
        self._frames = 0
        self._game_over_for = 0.0

    # ── Main loop ─────────────────────────────────────────────────────────────

    def run(self) -> int:
        """Play until the player quits (or ``--max-seconds`` runs out)."""
        import pygame

        self._banner()
        clock = pygame.time.Clock()
        fps = max(1, self.settings.window.fps)
        started = time.monotonic()

        try:
            while self.running:
                # Headless runs use a fixed step so a smoke test is reproducible
                # and does not depend on how fast the machine happens to be.
                raw_dt = (1.0 / fps) if self.settings.headless else clock.tick(fps) / 1000.0
                dt = min(raw_dt, MAX_FRAME_DT)

                self._handle_intents()
                if not self.running:
                    break

                events = self._step(dt)
                self.renderer.draw(
                    self.world,
                    self.brains.brains,
                    debug=self.debug,
                    overlay=self._overlay(),
                    events=events,
                )

                self._elapsed += dt
                self._frames += 1
                if self.settings.max_seconds and self._elapsed >= self.settings.max_seconds:
                    self.running = False
        except KeyboardInterrupt:
            print("\nInterrupted.")
        finally:
            self._shutdown(time.monotonic() - started)

        return 0

    def _step(self, dt: float) -> List[GameEvent]:
        """Advance one frame of simulation; returns this frame's events."""
        world = self.world
        active = world.phase is GamePhase.PLAYING and not world.paused

        if active:
            if self.autopilot:
                # The tree owns Pac-Man now; a stale key press must not leak
                # into world._apply_pacman_input and fight it for the wheel.
                world.pacman.desired_direction = Direction.NONE
            self.brains.tick_all(dt)

        events = world.update(dt)
        self._maybe_loop(dt)
        if self.debug:
            for event in events:
                if event.kind not in ("pellet",):
                    print(f"[{self._elapsed:7.2f}s] {event.kind}: {event.message or event.tile}")
        return events

    def _maybe_loop(self, dt: float) -> None:
        """With ``--loop``, deal a new game a few seconds after GAME OVER.

        The pause is not cosmetic: the final board is the only feedback an
        unattended run gives about how the autopilot died, and cutting to a
        fresh maze instantly throws that away.
        """
        if not self.settings.loop:
            return
        if self.world.phase is not GamePhase.GAME_OVER:
            self._game_over_for = 0.0
            return
        self._game_over_for += dt
        if self._game_over_for >= GAME_OVER_LINGER:
            self._game_over_for = 0.0
            self._restart()

    # ── Player intents ────────────────────────────────────────────────────────

    def _handle_intents(self) -> None:
        from pacman_bteng.gui import Intent

        for intent in self.input.poll(self.world):
            if intent is Intent.QUIT:
                self.running = False
            elif intent is Intent.PAUSE:
                self.world.paused = not self.world.paused
            elif intent is Intent.RESTART:
                self._restart()
            elif intent is Intent.TOGGLE_DEBUG:
                self.debug = not self.debug
            elif intent is Intent.TOGGLE_PANEL:
                self._toggle_panel()
            elif intent is Intent.TOGGLE_AUTOPILOT:
                self._toggle_autopilot()
            elif intent is Intent.TOGGLE_VISUALS:
                self._cycle_visuals()

    def _restart(self) -> None:
        self.world.start_level(self.settings.level, full_reset=True)
        self.world.paused = False
        self.brains.reset_all()
        self.renderer.reset_effects()

    def _toggle_panel(self) -> None:
        """Rebuild the window without the side panel (and back)."""
        self.show_panel = not self.show_panel
        self._rebuild_renderer()

    def _rebuild_renderer(self) -> None:
        """Recompute the layout and swap in a fresh Renderer.

        Both runtime toggles that change the window (TAB and F3) go through
        here, so there is exactly one place that knows how a Renderer is built.
        """
        from pacman_bteng.gui import Layout, Renderer, Theme

        self.layout = Layout.compute(
            self.maze,
            self.settings.window.tile_size,
            show_panel=self.show_panel,
            max_size=_screen_limit(self.settings),
        )
        self.renderer.close()
        self.renderer = Renderer(
            self.layout,
            Theme.for_style(self.style),
            show_panel=self.show_panel,
            caption=self.settings.window.caption,
            headless=self.settings.headless,
            style=self.style,
        )

    def _cycle_visuals(self) -> None:
        """Swap between the lite and deluxe presets without losing the game.

        The window is rebuilt rather than repainted because the two presets have
        different palettes and the deluxe one owns cached glow surfaces; making
        a Renderer disposable is far simpler than making one mutable.
        """
        from pacman_bteng.gui.theme import VisualStyle

        self.style = (
            VisualStyle.DELUXE if self.style is VisualStyle.LITE else VisualStyle.LITE
        )
        self.settings.window.visuals = self.style.value
        self._rebuild_renderer()

    def _toggle_autopilot(self) -> None:
        self.autopilot = not self.autopilot
        try:
            self.brains.set_autopilot(self.autopilot, self.settings)
        except BrainError as exc:
            self.autopilot = False
            print(f"autopilot unavailable: {exc}", file=sys.stderr)
        self.world.pacman.desired_direction = Direction.NONE

    # ── Presentation ──────────────────────────────────────────────────────────

    def _overlay(self) -> str:
        if self.world.paused:
            return "PAUSED"
        return _OVERLAYS.get(self.world.phase, "")

    def _banner(self) -> None:
        drivers = ", ".join(brain.name for brain in self.brains)
        print(f"pac-man x BTEng   maze {self.maze.name} ({self.maze.width}x{self.maze.height}), "
              f"{self.maze.total_pellets()} pellets")
        print(f"  window     {self.layout.width}x{self.layout.height} "
              f"(tile {self.layout.tile}px, {self.style.value} visuals)"
              + ("  [headless]" if self.settings.headless else ""))
        print(f"  trees      {drivers or '(none)'}")
        print(f"  introspect {self.introspection.summary()}")
        for warning in self.introspection.warnings:
            print(f"  warning    {warning}", file=sys.stderr)
        if self.introspection.endpoint:
            print("  watch it   ./run.sh tree blinky   |   ./run.sh viewer")
        print()

    def _shutdown(self, wall_time: float) -> None:
        self.brains.shutdown()
        self.introspection.close()
        self.renderer.close()
        fps = self._frames / wall_time if wall_time > 0 else 0.0
        print(
            f"\n{self.world.status_line()}  "
            f"({self._frames} frames, {self._elapsed:.1f}s game time, {fps:.0f} fps)"
        )


def _screen_limit(settings: Settings) -> Optional[tuple]:
    """How large the window may be, or None when it does not matter.

    Asking SDL for the desktop size needs an initialised video subsystem, which
    is exactly what a headless run does not have — so headless skips the check
    and trusts the configured tile size.
    """
    if settings.headless:
        return None
    try:
        import pygame

        pygame.display.init()
        info = pygame.display.Info()
        # Leave room for the window decorations and a panel/dock.
        return (info.current_w - 80, info.current_h - 120)
    except Exception:  # noqa: BLE001 - a missing display is not fatal here
        return None


def run(settings: Settings) -> int:
    """Build and run the app.  The entry point ``cli.main`` calls this."""
    return PacmanApp(settings).run()
