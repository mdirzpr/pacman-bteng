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
from typing import Dict, List

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
}

#: One line, shown in the bottom bar.  Kept here next to the key maps so the
#: hint can never drift away from what the keys actually do.
KEY_HINT = ("ARROWS/WASD move   SPACE pause   R restart   TAB panel   "
            "F1 debug   F2 auto   F3 visuals   ESC quit")


class InputHandler:
    """Drains the pygame event queue once per frame."""

    def __init__(self) -> None:
        #: Last direction the player asked for, kept only for introspection —
        #: the authoritative copy lives on ``world.pacman``.
        self.last_request: Direction = Direction.NONE

    def poll(self, world: GameWorld) -> List[Intent]:
        """Consume every pending event; return the non-movement intents.

        Movement keys are applied to *world* as a side effect.  The intent list
        preserves press order and may contain duplicates — the app is free to
        collapse them, but two quick pause taps really were two taps.
        """
        intents: List[Intent] = []

        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                intents.append(Intent.QUIT)
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
