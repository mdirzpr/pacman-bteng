"""The map is content, and content gets edited — these tests guard the edits.

Nothing here checks the parser's Python for its own sake.  Every case is a
property the *shipped* ``classic.txt`` has to keep having for the game to be
playable at all: a rectangle, five spawns, a sealed pen with exactly one way
out, one wrap-around row, and no second island Pac-Man can never reach.
"""

from __future__ import annotations

import pytest
from conftest import CLASSIC_MAZE

from pacman_bteng.game import pathfinding as pf
from pacman_bteng.game.geometry import Direction
from pacman_bteng.game.maze import SPAWN_MARKERS, Maze, MazeError


# ── Shape ─────────────────────────────────────────────────────────────────────

def test_classic_is_28_by_31(maze: Maze) -> None:
    assert (maze.width, maze.height) == (28, 31)
    assert maze.name == "classic"


def test_every_row_is_the_same_width(maze: Maze) -> None:
    # The parser rejects ragged rows outright, so this can only fail if someone
    # changes that rule — which is worth knowing about immediately, since the
    # renderer indexes rows by column with no bounds check of its own.
    assert {len(row) for row in maze.rows} == {maze.width}


def test_the_board_still_holds_the_arcade_pellet_count(maze: Maze) -> None:
    # 240 in the arcade; this map trades two of them for the corridor the
    # ghosts use. The number itself does not matter — noticing when it silently
    # changes does, because the level-clear condition counts down from it.
    assert len(maze.power_pellets) == 4
    assert maze.total_pellets() == len(maze.pellets) + 4
    assert maze.total_pellets() > 200


# ── Spawns ────────────────────────────────────────────────────────────────────

def test_all_five_spawns_are_present_and_standable(maze: Maze) -> None:
    assert set(maze.spawns) == set(SPAWN_MARKERS.values())
    for name, tile in maze.spawns.items():
        # A spawn on a wall would drop the agent into geometry it can never
        # step out of, and the failure would look like "the ghost is frozen".
        assert maze.is_walkable(tile, through_door=True), f"{name} spawns in a wall"


def test_three_ghosts_start_inside_the_pen_and_blinky_outside_it(maze: Maze) -> None:
    # This split is a rule, not a coincidence: GameWorld.reset_round decides
    # IN_HOUSE vs NORMAL purely from maze.is_house(spawn).
    assert not maze.is_house(maze.spawns["blinky"])
    for name in ("pinky", "inky", "clyde"):
        assert maze.is_house(maze.spawns[name]), f"{name} should start in the pen"


# ── The ghost house ───────────────────────────────────────────────────────────

def test_door_tiles_exist_and_are_not_ordinary_floor(maze: Maze) -> None:
    assert maze.door_tiles
    for tile in maze.door_tiles:
        assert maze.is_door(tile)
        assert not maze.is_walkable(tile)                       # Pac-Man: no
        assert maze.is_walkable(tile, through_door=True)        # a ghost: yes


def test_the_pen_is_sealed_except_for_the_door(maze: Maze) -> None:
    """Every way out of the house has to be the door.

    If a wall tile of the pen is ever deleted, ghosts stop queueing through the
    door and the whole release schedule becomes decoration — and nothing else
    in the game would report it.
    """
    assert maze.house_tiles
    for tile in maze.house_tiles:
        for _, neighbour in maze.walkable_neighbours(tile, through_door=False):
            assert maze.is_house(neighbour), f"{tile} leaks out to {neighbour}"


def test_door_exit_is_directly_above_the_door_and_walkable(maze: Maze) -> None:
    exit_tile = maze.door_exit()
    assert maze.is_walkable(exit_tile)
    assert not maze.is_house(exit_tile)
    below = Direction.DOWN.apply(exit_tile)
    assert below in maze.door_tiles


def test_house_centre_is_inside_the_house(maze: Maze) -> None:
    # Eaten ghosts are sent here by SetHouseTarget; a centre outside the pen
    # would leave the eyes circling forever.
    assert maze.is_house(maze.house_centre())


# ── Connectivity — the test that catches a map split in two ───────────────────

def test_every_walkable_tile_outside_the_pen_is_reachable_by_pacman(maze: Maze) -> None:
    """One flood fill from Pac-Man's spawn has to cover the whole board.

    This is the test that catches someone editing the map into two islands:
    the game would still start, still render, and still be unwinnable, because
    the pellets on the far island can never be eaten.
    """
    reachable = pf.reachable_tiles(
        maze.spawns["pacman"],
        lambda tile: maze.is_walkable(tile),
        wrap=maze.wrap,
    )
    walkable = {
        (x, y)
        for y in range(maze.height)
        for x in range(maze.width)
        if maze.is_walkable((x, y)) and not maze.is_house((x, y))
    }
    assert walkable - reachable == set()
    # And every pellet is on one of those tiles, so the board can be cleared.
    assert (maze.pellets | maze.power_pellets) <= reachable


def test_the_pen_is_unreachable_without_the_door_and_reachable_with_it(maze: Maze) -> None:
    start = maze.spawns["pacman"]
    closed = pf.reachable_tiles(start, lambda t: maze.is_walkable(t), wrap=maze.wrap)
    opened = pf.reachable_tiles(
        start, lambda t: maze.is_walkable(t, through_door=True), wrap=maze.wrap
    )
    assert maze.house_tiles.isdisjoint(closed)
    assert maze.house_tiles <= opened


# ── The tunnel ────────────────────────────────────────────────────────────────

def test_row_14_is_the_only_tunnel_row(maze: Maze) -> None:
    assert maze.tunnel_rows == (14,)


def test_wrap_only_wraps_on_the_tunnel_row(maze: Maze) -> None:
    row = maze.tunnel_rows[0]
    assert maze.wrap((-1, row)) == (maze.width - 1, row)
    assert maze.wrap((maze.width, row)) == (0, row)
    assert maze.wrap((13, row)) == (13, row)
    # Off the tunnel row a stray coordinate is handed straight back; it then
    # reads as VOID, which is what keeps "walked into the edge" a wall and not
    # a teleport.
    assert maze.wrap((-1, row - 1)) == (-1, row - 1)
    assert maze.wrap((maze.width, 0)) == (maze.width, 0)


def test_both_tunnel_mouths_are_walkable_and_flagged_as_tunnel(maze: Maze) -> None:
    row = maze.tunnel_rows[0]
    for tile in ((0, row), (maze.width - 1, row)):
        assert maze.is_walkable(tile)
        assert maze.is_tunnel(tile)      # ghosts crawl here (tunnel_speed_factor)
    assert not maze.is_tunnel((13, row))


# ── Malformed maps ────────────────────────────────────────────────────────────

def _write(tmp_path, text: str):
    path = tmp_path / "broken.txt"
    path.write_text(text, encoding="utf-8")
    return path


def test_ragged_rows_are_rejected(tmp_path) -> None:
    ragged = "[maze]\n#####\n#SBPIC#\n#####\n[/maze]\n"
    with pytest.raises(MazeError, match="different widths"):
        Maze.from_file(_write(tmp_path, ragged))


def test_a_missing_spawn_marker_is_rejected(tmp_path) -> None:
    # Pac-Man alone: GameWorld would otherwise KeyError deep inside its
    # constructor, which is a much worse way to learn the map is incomplete.
    no_ghosts = "[maze]\n#####\n#S..#\n#####\n[/maze]\n"
    with pytest.raises(MazeError, match="missing spawn"):
        Maze.from_file(_write(tmp_path, no_ghosts))


def test_an_empty_maze_block_is_rejected(tmp_path) -> None:
    with pytest.raises(MazeError, match="no rows"):
        Maze.from_file(_write(tmp_path, "[maze]\n[/maze]\n"))


def test_the_shipped_file_is_the_one_being_tested() -> None:
    # Cheap guard against the fixture quietly pointing somewhere else.
    assert CLASSIC_MAZE.exists()
    assert "[maze]" in CLASSIC_MAZE.read_text(encoding="utf-8")
