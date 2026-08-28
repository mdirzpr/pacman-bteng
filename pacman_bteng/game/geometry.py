"""Grid geometry: tile coordinates and the four cardinal directions.

Everything in the game world is expressed in *tile* units — the maze is a
28 x 31 grid and an agent's position is a tile plus a fractional progress
toward the next one.  Pixels appear only in :mod:`pacman_bteng.gui`.
"""

from __future__ import annotations

from enum import Enum
from typing import Iterator, Tuple

#: A tile coordinate, ``(column, row)`` with the origin at the top-left.
Tile = Tuple[int, int]


class Direction(Enum):
    """A cardinal direction, or ``NONE`` for "standing on a tile centre".

    ``NONE`` is the *decision point* marker: the world clears an agent's
    direction the instant it arrives on a tile, and a controller (the behavior
    tree for ghosts, the keyboard for Pac-Man) chooses the next one.
    """

    NONE  = (0, 0)
    UP    = (0, -1)
    DOWN  = (0, 1)
    LEFT  = (-1, 0)
    RIGHT = (1, 0)

    @property
    def delta(self) -> Tile:
        """The ``(dx, dy)`` step this direction represents."""
        return self.value

    @property
    def opposite(self) -> "Direction":
        dx, dy = self.value
        return Direction((-dx, -dy))

    @property
    def is_moving(self) -> bool:
        return self is not Direction.NONE

    def apply(self, tile: Tile, steps: int = 1) -> Tile:
        """Return *tile* moved *steps* tiles along this direction."""
        dx, dy = self.value
        return (tile[0] + dx * steps, tile[1] + dy * steps)

    @classmethod
    def from_delta(cls, dx: int, dy: int) -> "Direction":
        """Map a unit step back to a Direction (``NONE`` if it is not one)."""
        try:
            return cls((dx, dy))
        except ValueError:
            return cls.NONE

    @classmethod
    def between(cls, origin: Tile, target: Tile) -> "Direction":
        """Direction of the single step from *origin* to *target*."""
        return cls.from_delta(target[0] - origin[0], target[1] - origin[1])

    def __str__(self) -> str:  # noqa: D105 - shown in the HUD / BT viewer
        return self.name


#: The four real directions, in the order the arcade ghosts break ties:
#: up, left, down, right.  Keeping this order is what makes two ghosts with the
#: same target still pick reproducible — and slightly different — routes.
MOVES: Tuple[Direction, ...] = (
    Direction.UP,
    Direction.LEFT,
    Direction.DOWN,
    Direction.RIGHT,
)


def manhattan(a: Tile, b: Tile) -> int:
    """Manhattan distance in tiles."""
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def euclidean(a: Tile, b: Tile) -> float:
    """Straight-line distance in tiles — the arcade ghosts' own metric."""
    dx = a[0] - b[0]
    dy = a[1] - b[1]
    return (dx * dx + dy * dy) ** 0.5


def neighbours(tile: Tile) -> Iterator[Tuple[Direction, Tile]]:
    """Yield ``(direction, tile)`` for the four tiles around *tile*."""
    for direction in MOVES:
        yield direction, direction.apply(tile)
