"""Keyboard input — the only place the GUI is allowed to steer the game.

The handler deliberately does two different jobs and keeps them apart:

* **Movement** is written straight into ``world.pacman.desired_direction``.
  That is a *request*, not a move: the world applies it at the next decision
  point (see the decision-point contract in ``game/entities.py``), which is
  what lets you press "up" slightly early and still take the corner.
* **Everything else** is returned as an :class:`Intent`.  The GUI has no
  business pausing, restarting or toggling autopilot on its own — those change
  application state, so :class:`~pacman_bteng.app.PacmanApp` decides what they
  mean and this module only reports that a key was pressed.
"""

from __future__ import annotations

from enum import Enum
from typing import Callable, Dict, List, Optional, Tuple

import pygame

from pacman_bteng.game.geometry import Direction
from pacman_bteng.game.world import GameWorld


class Intent(Enum):
    """A non-movement request from the player, for the app to act on."""

    QUIT = "quit"
    PAUSE = "pause"
    RESTART = "restart"
    TOGGLE_DEBUG = "toggle_debug"
    TOGGLE_PANEL = "toggle_panel"
    TOGGLE_AUTOPILOT = "toggle_autopilot"
    TOGGLE_VISUALS = "toggle_visuals"
    TOGGLE_MUTE = "toggle_mute"
    START = "start"
    TAB_TREES = "tab_trees"
    TAB_STATS = "tab_stats"
    TAB_LOG = "tab_log"


#: Side-panel tab each tab intent selects.  The names are the ones the HUD's
#: dock draws, so the app can hand the string straight to the renderer.
TAB_BY_INTENT: Dict[Intent, str] = {
    Intent.TAB_TREES: "trees",
    Intent.TAB_STATS: "stats",
    Intent.TAB_LOG: "log",
}
INTENT_BY_TAB: Dict[str, Intent] = {tab: i for i, tab in TAB_BY_INTENT.items()}


# ── Key maps ──────────────────────────────────────────────────────────────────
# Arrows and WASD both drive Pac-Man; WASD is there so the left hand can steer
# while the right hand pokes at the debug toggles.
_MOVEMENT_KEYS: Dict[int, Direction] = {
    pygame.K_UP: Direction.UP,
    pygame.K_w: Direction.UP,
    pygame.K_DOWN: Direction.DOWN,
    pygame.K_s: Direction.DOWN,
    pygame.K_LEFT: Direction.LEFT,
    pygame.K_a: Direction.LEFT,
    pygame.K_RIGHT: Direction.RIGHT,
    pygame.K_d: Direction.RIGHT,
}

# The toggles avoid every WASD letter on purpose — 'd' is "right", not "debug".
_INTENT_KEYS: Dict[int, Intent] = {
    pygame.K_ESCAPE: Intent.QUIT,
    pygame.K_q: Intent.QUIT,
    pygame.K_SPACE: Intent.PAUSE,
    pygame.K_p: Intent.PAUSE,
    pygame.K_r: Intent.RESTART,
    pygame.K_TAB: Intent.TOGGLE_PANEL,
    pygame.K_F1: Intent.TOGGLE_DEBUG,
    pygame.K_F2: Intent.TOGGLE_AUTOPILOT,
    pygame.K_F3: Intent.TOGGLE_VISUALS,
    pygame.K_m: Intent.TOGGLE_MUTE,
    pygame.K_1: Intent.TAB_TREES,
    pygame.K_2: Intent.TAB_STATS,
    pygame.K_3: Intent.TAB_LOG,
    pygame.K_RETURN: Intent.START,
    pygame.K_KP_ENTER: Intent.START,
}

#: One line, shown in the bottom bar.  Kept here next to the key maps so the
#: hint can never drift away from what the keys actually do.
KEY_HINT = ("ARROWS/WASD move   SPACE pause   R restart   TAB panel   "
            "F1 debug   F2 auto   F3 visuals   1-3 tabs   M mute   ESC quit")


class InputHandler:
    """Drains the pygame event queue once per frame."""

    def __init__(self) -> None:
        #: Last direction the player asked for, kept only for introspection —
        #: the authoritative copy lives on ``world.pacman``.
        self.last_request: Direction = Direction.NONE

    def poll(
        self,
        world: GameWorld,
        hit_test: Optional[Callable[[Tuple[int, int]], Optional[Intent]]] = None,
    ) -> List[Intent]:
        """Consume every pending event; return the non-movement intents.

        Movement keys are applied to *world* as a side effect.  The intent list
        preserves press order and may contain duplicates — the app is free to
        collapse them, but two quick pause taps really were two taps.

        *hit_test* maps a left-click position to an intent (or ``None``); it is
        how the side panel's tab dock is clickable without this module knowing
        where the dock is.
        """
        intents: List[Intent] = []

        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                intents.append(Intent.QUIT)
                continue
            if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                hit = hit_test(event.pos) if hit_test is not None else None
                if hit is not None:
                    intents.append(hit)
                continue
            if event.type != pygame.KEYDOWN:
                continue

            direction = _MOVEMENT_KEYS.get(event.key)
            if direction is not None:
                # Buffered, not applied: the world turns this into a real move
                # only when Pac-Man is standing on a tile centre.
                world.pacman.desired_direction = direction
                self.last_request = direction
                continue

            intent = _INTENT_KEYS.get(event.key)
            if intent is not None:
                intents.append(intent)

        return intents
