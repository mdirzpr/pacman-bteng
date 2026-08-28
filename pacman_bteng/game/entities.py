"""Agents: Pac-Man and the four ghosts.

An agent is deliberately dumb — it holds position, speed and state, and knows
how to slide from one tile to the next.  *What* direction to take is decided
from outside: by the keyboard for Pac-Man, by a BTEng behavior tree for every
ghost (and for Pac-Man too in autopilot mode).

THE DECISION-POINT CONTRACT
---------------------------
:meth:`Agent.advance` clears ``direction`` to ``Direction.NONE`` the moment the
agent lands on a tile centre.  ``direction is NONE`` therefore means "this agent
is waiting for a decision", and it is the single handshake between the game loop
and every controller.  Nothing else may clear it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Tuple

from pacman_bteng.game.geometry import Direction, Tile
from pacman_bteng.game.maze import Maze


class GhostState(Enum):
    """Where a ghost is in its life cycle.

    The behavior tree branches on exactly these — see ``bt_structures/*.xml``.
    """

    IN_HOUSE   = "in_house"     # parked in the pen, waiting for its release timer
    LEAVING    = "leaving"      # walking out through the door
    NORMAL     = "normal"       # scatter or chase, depending on the global mode
    FRIGHTENED = "frightened"   # a power pellet is active — flee, and be edible
    EATEN      = "eaten"        # eyes only, heading back to the house


@dataclass
class Agent:
    """Something that walks the maze one tile at a time."""

    name: str
    home: Tile
    tile: Tile
    direction: Direction = Direction.NONE
    #: Fraction of the way from ``tile`` to the next tile along ``direction``.
    progress: float = 0.0
    #: Tiles per second.
    speed: float = 8.0
    #: The direction taken into the current tile; ghosts may not reverse onto it.
    last_direction: Direction = Direction.NONE
    #: Tile the controlling behavior tree picked last tick — drawn by the debug
    #: overlay and listed in the side panel.  On Agent rather than Ghost because
    #: Pac-Man's autopilot aims at tiles too, and a target you cannot see is
    #: half the debugging value of a behavior tree gone.
    target: Optional[Tile] = None

    # ── Position ──────────────────────────────────────────────────────────────

    @property
    def position(self) -> Tuple[float, float]:
        """Continuous position in tile units, for the renderer.

        While waiting for a decision (``direction is NONE``) the agent sits
        exactly on its tile centre, so leftover progress never bleeds into a
        direction that has not been chosen yet.
        """
        if not self.direction.is_moving:
            return (float(self.tile[0]), float(self.tile[1]))
        dx, dy = self.direction.delta
        return (self.tile[0] + dx * self.progress, self.tile[1] + dy * self.progress)

    @property
    def is_deciding(self) -> bool:
        """True when the agent is parked on a tile centre awaiting a direction."""
        return not self.direction.is_moving

    def next_tile(self) -> Tile:
        """The tile this agent is currently sliding into (itself when parked)."""
        return self.direction.apply(self.tile) if self.direction.is_moving else self.tile

    # ── Movement ──────────────────────────────────────────────────────────────

    def face(self, direction: Direction, maze: Maze, *, through_door: bool = False) -> bool:
        """Commit to *direction* for one tile.  Returns False if it is blocked.

        Only meaningful at a decision point; the leftover ``progress`` from the
        previous tile is carried into the new direction so the agent keeps a
        constant speed around corners.
        """
        if not direction.is_moving:
            return False
        target = maze.wrap(direction.apply(self.tile))
        if not maze.is_walkable(target, through_door=through_door):
            return False
        self.direction = direction
        return True

    def advance(self, dt: float, maze: Maze) -> bool:
        """Slide for *dt* seconds.  Returns True if a new tile was reached.

        On arrival the direction is cleared (the decision-point contract) and
        the overshoot is kept in ``progress`` so it applies to the next leg.
        """
        if not self.direction.is_moving:
            return False

        self.progress += self.speed * dt
        if self.progress < 1.0:
            return False

        self.tile = maze.wrap(self.direction.apply(self.tile))
        self.last_direction = self.direction
        self.direction = Direction.NONE
        # Cap the carry-over: a long frame must never teleport an agent past a
        # decision point it should have stopped at.
        self.progress = min(self.progress - 1.0, 0.99)
        return True

    def teleport(self, tile: Tile, direction: Direction = Direction.NONE) -> None:
        """Drop the agent onto *tile*, cancelling any movement in progress."""
        self.tile = tile
        self.direction = direction
        self.last_direction = Direction.NONE
        self.progress = 0.0
        self.target = None

    def reset(self) -> None:
        """Send the agent back to its spawn tile."""
        self.teleport(self.home)


@dataclass
class Pacman(Agent):
    """The player (or, with ``--auto``, another behavior tree)."""

    #: Direction the player has asked for; applied at the next decision point.
    desired_direction: Direction = Direction.NONE
    alive: bool = True

    def reset(self) -> None:
        super().reset()
        self.desired_direction = Direction.NONE
        self.alive = True


@dataclass
class Ghost(Agent):
    """One of the four ghosts, driven by its own behavior tree."""

    state: GhostState = GhostState.IN_HOUSE
    #: Tile this ghost retreats to in scatter mode (its personal corner).
    scatter_corner: Tile = (0, 0)
    #: Seconds remaining before this ghost is allowed to leave the house.
    release_timer: float = 0.0
    #: Set while the ghost is edible and blinking white (last seconds of a power
    #: pellet); purely cosmetic, the renderer reads it.
    blinking: bool = False
    #: Which branch of its tree the ghost is executing, for the HUD panel.
    behavior: str = ""

    @property
    def is_edible(self) -> bool:
        return self.state is GhostState.FRIGHTENED

    @property
    def is_hostile(self) -> bool:
        """True when touching Pac-Man costs him a life."""
        return self.state in (GhostState.NORMAL, GhostState.LEAVING)

    @property
    def may_pass_door(self) -> bool:
        """Ghosts cross the house door only on the way out or on the way back."""
        return self.state in (GhostState.LEAVING, GhostState.EATEN, GhostState.IN_HOUSE)

    def reset(self, *, state: GhostState = GhostState.IN_HOUSE) -> None:
        super().reset()
        self.state = state
        self.target = None
        self.blinking = False
        self.behavior = ""
