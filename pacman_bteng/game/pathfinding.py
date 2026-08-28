"""Ghost navigation: the arcade rule, and a BFS for when it is not enough.

Two independent strategies, both returning "which direction do I take out of
this tile":

``greedy_step``
    The original arcade algorithm.  Look at the tiles around you, drop the one
    you came from, and take whichever remaining tile is closest *as the crow
    flies* to the target.  It has no memory and no lookahead, which is exactly
    why the arcade ghosts can be out-manoeuvred around a corner.

``bfs_step``
    A breadth-first shortest path.  Always finds a route if one exists, so it is
    what a ghost with its eyes returning to the house uses — and what makes a
    "cheating" ghost profile if you point it at Pac-Man.

Both take a ``passable`` predicate rather than a maze, so the caller decides
whether the ghost-house door is open for this particular agent.
"""

from __future__ import annotations

import random
from collections import deque
from typing import Callable, Dict, List, Optional, Sequence

from pacman_bteng.game.geometry import MOVES, Direction, Tile, euclidean

#: ``passable(tile) -> bool`` — may this agent stand on this tile right now?
Passable = Callable[[Tile], bool]
#: ``wrap(tile) -> tile`` — normalise a tile through the side tunnels.
Wrap = Callable[[Tile], Tile]


def _identity(tile: Tile) -> Tile:
    return tile


#: Raw steps for the inlined neighbour walk in distance_field, in MOVES order.
_DELTAS = tuple(direction.delta for direction in MOVES)


def legal_moves(
    tile: Tile,
    passable: Passable,
    *,
    forbidden: Direction = Direction.NONE,
    wrap: Wrap = _identity,
) -> List[Direction]:
    """Directions an agent may take out of *tile*.

    *forbidden* removes a single direction — the reversal a ghost is not
    allowed to make.  If that leaves nothing at all (a dead end), the reversal
    is handed back rather than freezing the ghost.
    """
    options = [
        direction
        for direction in MOVES
        if direction is not forbidden and passable(wrap(direction.apply(tile)))
    ]
    if options:
        return options
    if forbidden.is_moving and passable(wrap(forbidden.apply(tile))):
        return [forbidden]
    return []


def greedy_step(
    tile: Tile,
    target: Tile,
    passable: Passable,
    *,
    forbidden: Direction = Direction.NONE,
    wrap: Wrap = _identity,
) -> Direction:
    """The arcade rule: step to the neighbour nearest *target*.

    Ties break in UP, LEFT, DOWN, RIGHT order — the original's own priority.
    Returns ``Direction.NONE`` when the agent is walled in.
    """
    best: Direction = Direction.NONE
    best_distance = float("inf")
    for direction in legal_moves(tile, passable, forbidden=forbidden, wrap=wrap):
        distance = euclidean(wrap(direction.apply(tile)), target)
        if distance < best_distance:
            best, best_distance = direction, distance
    return best


def flee_step(
    tile: Tile,
    threat: Tile,
    passable: Passable,
    *,
    forbidden: Direction = Direction.NONE,
    wrap: Wrap = _identity,
) -> Direction:
    """Step to the neighbour *furthest* from *threat* — greedy_step inverted."""
    best: Direction = Direction.NONE
    best_distance = -1.0
    for direction in legal_moves(tile, passable, forbidden=forbidden, wrap=wrap):
        distance = euclidean(wrap(direction.apply(tile)), threat)
        if distance > best_distance:
            best, best_distance = direction, distance
    return best


def random_step(
    tile: Tile,
    passable: Passable,
    *,
    forbidden: Direction = Direction.NONE,
    wrap: Wrap = _identity,
    rng: Optional[random.Random] = None,
) -> Direction:
    """Any legal direction, chosen at random. The last-resort "keep moving"."""
    options = legal_moves(tile, passable, forbidden=forbidden, wrap=wrap)
    if not options:
        return Direction.NONE
    return (rng or random).choice(options)


def bfs_path(
    start: Tile,
    goal: Tile,
    passable: Passable,
    *,
    forbidden: Direction = Direction.NONE,
    wrap: Wrap = _identity,
    max_nodes: int = 4096,
) -> List[Tile]:
    """Shortest tile path from *start* to *goal*, excluding *start* itself.

    Returns ``[]`` when the goal is unreachable (or the search hit
    *max_nodes*).  The ban on reversing applies to the first step only, exactly
    like the arcade rule it complements.
    """
    start = wrap(start)
    goal = wrap(goal)
    if start == goal:
        return []

    came_from: Dict[Tile, Tile] = {start: start}
    queue = deque([start])
    first = True
    visited = 0

    while queue and visited < max_nodes:
        current = queue.popleft()
        visited += 1
        moves = (
            legal_moves(current, passable, forbidden=forbidden, wrap=wrap)
            if first
            else legal_moves(current, passable, wrap=wrap)
        )
        first = False
        for direction in moves:
            nxt = wrap(direction.apply(current))
            if nxt in came_from:
                continue
            came_from[nxt] = current
            if nxt == goal:
                return _unroll(came_from, start, goal)
            queue.append(nxt)
    return []


def _unroll(came_from: Dict[Tile, Tile], start: Tile, goal: Tile) -> List[Tile]:
    path: List[Tile] = []
    node = goal
    while node != start:
        path.append(node)
        node = came_from[node]
    path.reverse()
    return path


def bfs_step(
    tile: Tile,
    target: Tile,
    passable: Passable,
    *,
    forbidden: Direction = Direction.NONE,
    wrap: Wrap = _identity,
) -> Direction:
    """First direction of the shortest path to *target*.

    Falls back to :func:`greedy_step` when the target is unreachable — an eaten
    ghost must never stall just because the player is standing in a doorway.
    """
    path = bfs_path(tile, target, passable, forbidden=forbidden, wrap=wrap)
    if not path:
        return greedy_step(tile, target, passable, forbidden=forbidden, wrap=wrap)
    return Direction.between(tile, _unwrap_neighbour(tile, path[0]))


def _unwrap_neighbour(tile: Tile, neighbour: Tile) -> Tile:
    """Undo tunnel wrapping so ``Direction.between`` sees a unit step.

    Crossing the tunnel makes ``neighbour`` sit 27 columns away; the step that
    gets there is still a single LEFT or RIGHT.
    """
    dx = neighbour[0] - tile[0]
    if dx > 1:
        return (tile[0] - 1, neighbour[1])
    if dx < -1:
        return (tile[0] + 1, neighbour[1])
    return neighbour


#: name → step function, so a behavior tree can pick its navigator from XML
#: (``<StepTowardTarget pathfinder="bfs"/>``).
STEP_STRATEGIES: Dict[str, Callable[..., Direction]] = {
    "greedy": greedy_step,
    "bfs": bfs_step,
}


def resolve_strategy(name: str) -> Callable[..., Direction]:
    """Look up a navigator by name, defaulting to the arcade rule."""
    return STEP_STRATEGIES.get(str(name).strip().lower(), greedy_step)


def distance_field(
    origin: Tile,
    passable: Passable,
    *,
    wrap: Wrap = _identity,
    max_nodes: int = 4096,
    max_distance: Optional[int] = None,
) -> Dict[Tile, int]:
    """Corridor distance from *origin* to every tile it can reach.

    One BFS answers "how far is everything from here", which is what you want
    when several things need measuring against one point: four ghosts against
    Pac-Man, or four candidate tiles against one ghost.  Straight-line distance
    is the wrong answer in a maze often enough to lose a life over — a ghost
    three tiles away through a wall is not a threat, and one seven tiles down an
    open corridor is.

    *max_distance* stops the wavefront early.  A guard asking "is anything
    within seven tiles" learns nothing from the other 260 tiles of the board,
    and this runs on every agent on every frame — bounding it is the difference
    between a third of a millisecond and five.  Tiles beyond the bound are
    simply absent from the result, which reads the same as unreachable to every
    caller.

    The neighbour walk is inlined rather than going through
    :func:`legal_moves`: at sixty frames a second this is the hottest loop in
    the project, and the list it builds per tile was most of the cost.
    """
    origin = wrap(origin)
    dist: Dict[Tile, int] = {origin: 0}
    queue = deque([origin])
    visited = 0
    while queue and visited < max_nodes:
        current = queue.popleft()
        visited += 1
        step = dist[current] + 1
        if max_distance is not None and step > max_distance:
            continue
        cx, cy = current
        for dx, dy in _DELTAS:
            nxt = wrap((cx + dx, cy + dy))
            if nxt not in dist and passable(nxt):
                dist[nxt] = step
                queue.append(nxt)
    return dist


def reachable_tiles(start: Tile, passable: Passable, *, wrap: Wrap = _identity) -> set:
    """Every tile reachable from *start* — used by the maze self-test."""
    seen = {wrap(start)}
    queue = deque(seen)
    while queue:
        current = queue.popleft()
        for direction in legal_moves(current, passable, wrap=wrap):
            nxt = wrap(direction.apply(current))
            if nxt not in seen:
                seen.add(nxt)
                queue.append(nxt)
    return seen


def order_by_distance(tiles: Sequence[Tile], origin: Tile) -> List[Tile]:
    """*tiles* sorted nearest-first from *origin* (straight-line)."""
    return sorted(tiles, key=lambda t: euclidean(origin, t))
