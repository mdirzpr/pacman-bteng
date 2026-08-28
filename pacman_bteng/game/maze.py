"""Maze layout — the immutable half of the game world.

A maze is loaded from an ASCII map (``assets/mazes/classic.txt``).  It owns the
walls, the ghost-house door, the tunnel and the *initial* pellet layout; the
pellets that are still on the board at any moment belong to
:class:`~pacman_bteng.game.world.GameWorld`, because those change while the
maze does not.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, FrozenSet, Iterator, List, Sequence, Tuple

from pacman_bteng.game.geometry import MOVES, Direction, Tile

# ── Map characters ────────────────────────────────────────────────────────────

WALL = "#"
VOID = "X"
DOOR = "-"
PELLET = "."
POWER = "o"
EMPTY = " "

#: spawn marker → agent name
SPAWN_MARKERS: Dict[str, str] = {
    "S": "pacman",
    "B": "blinky",
    "P": "pinky",
    "I": "inky",
    "C": "clyde",
}

_MAZE_BEGIN = "[maze]"
_MAZE_END = "[/maze]"


class MazeError(ValueError):
    """The map file is malformed — ragged rows, no maze block, no spawns."""


@dataclass(frozen=True)
class Maze:
    """A parsed maze: walls, door, tunnel row(s), spawns and pellet layout.

    Instances are immutable and safe to share between the world, the behavior
    trees and the renderer.
    """

    name: str
    rows: Tuple[str, ...]
    spawns: Dict[str, Tile]
    pellets: FrozenSet[Tile]
    power_pellets: FrozenSet[Tile]
    door_tiles: FrozenSet[Tile]
    house_tiles: FrozenSet[Tile]
    tunnel_rows: Tuple[int, ...] = field(default=())

    # ── Construction ──────────────────────────────────────────────────────────

    @classmethod
    def from_file(cls, path: str | Path) -> "Maze":
        """Load a maze from an ASCII map file (see the header of classic.txt)."""
        path = Path(path)
        return cls.from_string(path.read_text(encoding="utf-8"), name=path.stem)

    @classmethod
    def from_string(cls, text: str, name: str = "maze") -> "Maze":
        rows = cls._extract_rows(text)
        if not rows:
            raise MazeError(f"maze {name!r}: no rows found between {_MAZE_BEGIN} and {_MAZE_END}")

        widths = {len(r) for r in rows}
        if len(widths) != 1:
            raise MazeError(
                f"maze {name!r}: rows have different widths {sorted(widths)} — "
                "every row must have exactly the same number of characters"
            )

        spawns: Dict[str, Tile] = {}
        pellets: set[Tile] = set()
        power: set[Tile] = set()
        doors: set[Tile] = set()

        for y, row in enumerate(rows):
            for x, char in enumerate(row):
                tile = (x, y)
                if char == PELLET:
                    pellets.add(tile)
                elif char == POWER:
                    power.add(tile)
                elif char == DOOR:
                    doors.add(tile)
                elif char in SPAWN_MARKERS:
                    spawns[SPAWN_MARKERS[char]] = tile

        missing = set(SPAWN_MARKERS.values()) - set(spawns)
        if missing:
            raise MazeError(
                f"maze {name!r}: missing spawn marker(s) for {sorted(missing)} — "
                f"expected one of {sorted(SPAWN_MARKERS)} per agent"
            )

        # The tunnel rows are the ones whose first and last tile are both
        # walkable: those are the only places where wrapping makes sense.
        tunnel_rows = tuple(
            y for y, row in enumerate(rows)
            if row[0] not in (WALL, VOID) and row[-1] not in (WALL, VOID)
        )

        house = cls._flood_house(rows, doors)
        return cls(
            name=name,
            rows=tuple(rows),
            spawns=spawns,
            pellets=frozenset(pellets),
            power_pellets=frozenset(power),
            door_tiles=frozenset(doors),
            house_tiles=frozenset(house),
            tunnel_rows=tunnel_rows,
        )

    @staticmethod
    def _extract_rows(text: str) -> List[str]:
        """Return the lines between the ``[maze]`` / ``[/maze]`` markers.

        Without the markers the whole file is taken as the map, so a bare
        rectangle of characters is also a valid map file.
        """
        lines = text.splitlines()
        if _MAZE_BEGIN in (line.strip() for line in lines):
            out: List[str] = []
            inside = False
            for line in lines:
                stripped = line.strip()
                if stripped == _MAZE_BEGIN:
                    inside = True
                    continue
                if stripped == _MAZE_END:
                    break
                if inside:
                    out.append(line.rstrip("\n"))
            return out
        return [line for line in lines if line and not line.startswith("#")]

    @staticmethod
    def _flood_house(rows: Sequence[str], doors: set) -> set:
        """Tiles enclosed by the ghost house — flood fill inwards from the door."""
        if not doors:
            return set()
        height, width = len(rows), len(rows[0])
        # Start one tile *below* each door tile: that is the inside of the house.
        stack = [(x, y + 1) for (x, y) in doors]
        seen: set = set()
        while stack:
            x, y = stack.pop()
            if not (0 <= x < width and 0 <= y < height) or (x, y) in seen:
                continue
            if rows[y][x] in (WALL, VOID, DOOR):
                continue
            seen.add((x, y))
            for direction in MOVES:
                stack.append(direction.apply((x, y)))
        return seen

    # ── Geometry ──────────────────────────────────────────────────────────────

    @property
    def width(self) -> int:
        return len(self.rows[0])

    @property
    def height(self) -> int:
        return len(self.rows)

    def char_at(self, tile: Tile) -> str:
        x, y = tile
        if not (0 <= x < self.width and 0 <= y < self.height):
            return VOID
        return self.rows[y][x]

    def in_bounds(self, tile: Tile) -> bool:
        x, y = tile
        return 0 <= x < self.width and 0 <= y < self.height

    def wrap(self, tile: Tile) -> Tile:
        """Wrap a tile through the side tunnels.

        Only rows that are open at both ends wrap; anywhere else the coordinate
        is returned untouched (and will simply read as a wall).
        """
        x, y = tile
        if y in self.tunnel_rows:
            x %= self.width
        return (x, y)

    # ── Queries ───────────────────────────────────────────────────────────────

    def is_wall(self, tile: Tile) -> bool:
        return self.char_at(tile) in (WALL, VOID)

    def is_void(self, tile: Tile) -> bool:
        return self.char_at(tile) == VOID

    def is_door(self, tile: Tile) -> bool:
        return tile in self.door_tiles

    def is_house(self, tile: Tile) -> bool:
        return tile in self.house_tiles

    def is_tunnel(self, tile: Tile) -> bool:
        """True inside the wrap-around corridor (used to slow ghosts down)."""
        x, y = tile
        return y in self.tunnel_rows and (x < 6 or x >= self.width - 6)

    def is_walkable(self, tile: Tile, *, through_door: bool = False) -> bool:
        """Can an agent stand on *tile*?

        ``through_door`` is what separates ghosts from Pac-Man: only a ghost
        that is leaving or returning to the house may cross the door tiles.
        """
        tile = self.wrap(tile)
        if self.is_door(tile):
            return through_door
        return not self.is_wall(tile)

    def walkable_neighbours(
        self, tile: Tile, *, through_door: bool = False
    ) -> Iterator[Tuple[Direction, Tile]]:
        """Yield every ``(direction, tile)`` an agent may step to from *tile*."""
        for direction in MOVES:
            nxt = self.wrap(direction.apply(tile))
            if self.is_walkable(nxt, through_door=through_door):
                yield direction, nxt

    def door_exit(self) -> Tile:
        """The tile just outside (above) the ghost-house door.

        Ghosts head here to leave the house and to re-enter it after being
        eaten; it is the one tile every house route passes through.
        """
        if not self.door_tiles:
            return self.spawns["blinky"]
        xs = [x for x, _ in self.door_tiles]
        y = min(y for _, y in self.door_tiles)
        return (sum(xs) // len(xs), y - 1)

    def house_centre(self) -> Tile:
        """Middle of the ghost house — where eaten ghosts respawn."""
        if not self.house_tiles:
            return self.spawns["pinky"]
        xs = [x for x, _ in self.house_tiles]
        ys = [y for _, y in self.house_tiles]
        return (sum(xs) // len(xs), sum(ys) // len(ys))

    def total_pellets(self) -> int:
        return len(self.pellets) + len(self.power_pellets)

    def __repr__(self) -> str:  # noqa: D105
        return (
            f"Maze(name={self.name!r}, {self.width}x{self.height}, "
            f"pellets={len(self.pellets)}, power={len(self.power_pellets)})"
        )
