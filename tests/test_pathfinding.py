"""Navigation: the arcade rule, the BFS, and the tunnel that breaks both.

Most cases run on a hand-made ``passable`` predicate rather than on the real
maze — the point of taking a predicate instead of a Maze is that a five-tile
corridor is a legitimate world, and a five-tile corridor is far easier to
reason about than row 14 of ``classic.txt``.  The tunnel cases are the
exception: wrapping only exists on the real map.
"""

from __future__ import annotations

from typing import Set

from pacman_bteng.game import pathfinding as pf
from pacman_bteng.game.geometry import Direction, Tile, manhattan
from pacman_bteng.game.maze import Maze


def open_grid(width: int, height: int):
    """``passable`` for a rectangle with nothing in it."""
    return lambda tile: 0 <= tile[0] < width and 0 <= tile[1] < height


def only(tiles: Set[Tile]):
    """``passable`` for an explicit set of tiles — a corridor, a dead end."""
    return lambda tile: tile in tiles


# ── legal_moves ───────────────────────────────────────────────────────────────

def test_legal_moves_lists_the_open_neighbours_in_arcade_order() -> None:
    # UP, LEFT, DOWN, RIGHT is not cosmetic: it is the tie-break that makes two
    # ghosts aiming at the same tile still take reproducible routes.
    assert pf.legal_moves((2, 2), open_grid(5, 5)) == [
        Direction.UP,
        Direction.LEFT,
        Direction.DOWN,
        Direction.RIGHT,
    ]


def test_legal_moves_drops_the_banned_reversal() -> None:
    moves = pf.legal_moves((2, 2), open_grid(5, 5), forbidden=Direction.LEFT)
    assert Direction.LEFT not in moves
    assert set(moves) == {Direction.UP, Direction.DOWN, Direction.RIGHT}


def test_a_dead_end_hands_the_banned_reversal_back() -> None:
    """The one case where the no-reversing rule has to yield.

    A ghost at the end of a stub corridor has exactly one neighbour: the tile
    it just came from.  Returning [] here would freeze it in place forever, so
    the ban is lifted rather than enforced into a deadlock.
    """
    corridor = only({(0, 0), (1, 0), (2, 0)})
    assert pf.legal_moves((2, 0), corridor, forbidden=Direction.LEFT) == [Direction.LEFT]
    # ...and only in that case: with a second option open, the ban still holds.
    assert pf.legal_moves((1, 0), corridor, forbidden=Direction.LEFT) == [Direction.RIGHT]


def test_a_sealed_tile_has_no_moves_at_all() -> None:
    assert pf.legal_moves((0, 0), only({(0, 0)})) == []


# ── greedy_step ───────────────────────────────────────────────────────────────

def test_greedy_step_takes_the_neighbour_nearest_the_target() -> None:
    assert pf.greedy_step((2, 2), (4, 2), open_grid(5, 5)) is Direction.RIGHT
    assert pf.greedy_step((2, 2), (2, 0), open_grid(5, 5)) is Direction.UP


def test_greedy_step_honours_forbidden_and_breaks_ties_upward() -> None:
    # RIGHT is nearest but banned; UP and DOWN are then equidistant, and the
    # MOVES order picks UP.  That determinism is what makes a seeded run
    # reproducible frame for frame.
    step = pf.greedy_step((2, 2), (4, 2), open_grid(5, 5), forbidden=Direction.RIGHT)
    assert step is Direction.UP


def test_greedy_step_returns_none_when_walled_in() -> None:
    assert pf.greedy_step((0, 0), (4, 4), only({(0, 0)})) is Direction.NONE


def test_flee_step_is_greedy_step_inverted() -> None:
    assert pf.flee_step((2, 2), (4, 2), open_grid(5, 5)) is Direction.LEFT


# ── bfs_path ──────────────────────────────────────────────────────────────────

def test_bfs_path_is_shortest_and_excludes_the_start(maze: Maze) -> None:
    # Row 1 is a straight corridor, so the shortest route is the only route and
    # its length is the tile distance.
    path = pf.bfs_path((1, 1), (6, 1), lambda t: maze.is_walkable(t), wrap=maze.wrap)
    assert path == [(2, 1), (3, 1), (4, 1), (5, 1), (6, 1)]


def test_bfs_path_is_empty_for_an_unreachable_goal(maze: Maze) -> None:
    # (0, 0) is a wall: reachable by no route at all.
    assert pf.bfs_path((1, 1), (0, 0), lambda t: maze.is_walkable(t), wrap=maze.wrap) == []


def test_bfs_path_is_empty_when_start_equals_goal() -> None:
    assert pf.bfs_path((2, 2), (2, 2), open_grid(5, 5)) == []


def test_bfs_reaches_the_pen_only_through_the_door(maze: Maze) -> None:
    centre = maze.house_centre()
    start = maze.spawns["pacman"]
    assert pf.bfs_path(start, centre, lambda t: maze.is_walkable(t), wrap=maze.wrap) == []
    with_door = pf.bfs_path(
        start, centre, lambda t: maze.is_walkable(t, through_door=True), wrap=maze.wrap
    )
    assert with_door and with_door[-1] == centre


# ── bfs_step, and the tunnel ──────────────────────────────────────────────────

def test_bfs_step_agrees_with_the_first_tile_of_bfs_path(maze: Maze) -> None:
    passable = lambda tile: maze.is_walkable(tile)          # noqa: E731
    start, goal = (1, 1), (12, 1)
    path = pf.bfs_path(start, goal, passable, wrap=maze.wrap)
    step = pf.bfs_step(start, goal, passable, wrap=maze.wrap)
    assert maze.wrap(step.apply(start)) == path[0]


def test_a_step_through_the_tunnel_is_still_one_step(maze: Maze) -> None:
    """The ``_unwrap_neighbour`` case, and the reason it exists.

    Leaving (0, 14) westward lands on (27, 14): 27 columns away as raw
    coordinates, one step as movement.  Without the unwrap,
    ``Direction.between`` sees a delta of 27 and returns NONE — the eyes would
    stall at the tunnel mouth every time they tried to cross it.
    """
    row = maze.tunnel_rows[0]
    passable = lambda tile: maze.is_walkable(tile)          # noqa: E731
    start, goal = (0, row), (26, row)

    path = pf.bfs_path(start, goal, passable, wrap=maze.wrap)
    assert path[0] == (maze.width - 1, row)                 # the wrap really happens

    step = pf.bfs_step(start, goal, passable, wrap=maze.wrap)
    assert step is Direction.LEFT                           # not NONE, not a jump
    assert maze.wrap(step.apply(start)) == path[0]


def test_bfs_step_falls_back_to_greedy_when_the_goal_is_walled_off(maze: Maze) -> None:
    # An eaten ghost whose route is momentarily blocked must keep moving rather
    # than stand still, so an unreachable target degrades to the arcade rule.
    passable = lambda tile: maze.is_walkable(tile)          # noqa: E731
    step = pf.bfs_step((1, 1), (0, 0), passable, wrap=maze.wrap)
    assert step.is_moving


# ── random_step / reachable_tiles / order_by_distance ─────────────────────────

def test_random_step_stays_legal_under_any_seed() -> None:
    import random

    rng = random.Random(7)
    corridor = only({(0, 0), (1, 0), (2, 0)})
    for _ in range(20):
        step = pf.random_step((1, 0), corridor, rng=rng)
        assert step in (Direction.LEFT, Direction.RIGHT)


def test_random_step_returns_none_when_there_is_nowhere_to_go() -> None:
    assert pf.random_step((0, 0), only({(0, 0)})) is Direction.NONE


def test_reachable_tiles_includes_the_start_tile() -> None:
    assert pf.reachable_tiles((2, 2), only({(2, 2)})) == {(2, 2)}


def test_distance_field_measures_along_the_corridors(maze: Maze) -> None:
    """One BFS, every distance — and they are corridor distances, not sight.

    Straight-line distance is the wrong answer in a maze often enough to lose a
    life over: a ghost three tiles away through a wall is not a threat.
    """
    field = pf.distance_field((1, 1), lambda t: maze.is_walkable(t), wrap=maze.wrap)

    assert field[(1, 1)] == 0
    assert field[(6, 1)] == 5                      # straight down row 1
    assert (0, 0) not in field                     # a wall is not in the field
    # Every entry agrees with the path length BFS would have produced alone.
    for goal in ((6, 1), (1, 5), (12, 1)):
        assert field[goal] == len(
            pf.bfs_path((1, 1), goal, lambda t: maze.is_walkable(t), wrap=maze.wrap)
        )


def test_distance_field_crosses_the_tunnel(maze: Maze) -> None:
    row = maze.tunnel_rows[0]
    field = pf.distance_field((0, row), lambda t: maze.is_walkable(t), wrap=maze.wrap)
    assert field[(maze.width - 1, row)] == 1       # one step, not twenty-seven


def test_order_by_distance_is_nearest_first() -> None:
    assert pf.order_by_distance([(5, 0), (1, 0), (3, 0)], (0, 0)) == [(1, 0), (3, 0), (5, 0)]


# ── Strategy lookup ───────────────────────────────────────────────────────────

def test_resolve_strategy_maps_the_names_the_xml_uses() -> None:
    assert pf.resolve_strategy("greedy") is pf.greedy_step
    assert pf.resolve_strategy("bfs") is pf.bfs_step
    assert pf.resolve_strategy(" BFS ") is pf.bfs_step      # XML whitespace, XML case


def test_resolve_strategy_falls_back_to_the_arcade_rule() -> None:
    # A typo in a tree's pathfinder="" must not stop the game from starting;
    # the ghost just plays by the original rule instead.
    assert pf.resolve_strategy("astar") is pf.greedy_step
    assert pf.resolve_strategy("") is pf.greedy_step
    assert pf.resolve_strategy(None) is pf.greedy_step


# ── Bounded distance fields ───────────────────────────────────────────────────

def test_distance_field_measures_along_the_corridors(world) -> None:
    # The whole reason the path metric exists: two tiles either side of a wall
    # are adjacent by sight and a long walk apart in fact.
    passable = world.passable_for(world.pacman)
    field = pf.distance_field(world.pacman.tile, passable, wrap=world.wrap)
    for tile, steps in field.items():
        assert steps >= manhattan(world.pacman.tile, tile) or world.maze.is_tunnel(tile)


def test_distance_field_bound_stops_the_wavefront(world) -> None:
    # The bound is a performance promise, and it has to be a real one: this runs
    # for every agent on every frame.
    passable = world.passable_for(world.pacman)
    full = pf.distance_field(world.pacman.tile, passable, wrap=world.wrap)
    near = pf.distance_field(world.pacman.tile, passable, wrap=world.wrap, max_distance=5)

    assert len(near) < len(full)
    assert max(near.values()) <= 5
    # Everything it does report still agrees with the unbounded answer.
    assert all(full[tile] == steps for tile, steps in near.items())


def test_distance_field_agrees_with_bfs_path_length(world) -> None:
    passable = world.passable_for(world.pacman)
    start = world.pacman.tile
    field = pf.distance_field(start, passable, wrap=world.wrap)
    goal = (1, 1)
    assert field[goal] == len(pf.bfs_path(start, goal, passable, wrap=world.wrap))
