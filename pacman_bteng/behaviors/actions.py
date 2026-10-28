"""Action nodes — the four ghost personalities, and the one mover they share.

The library splits in two halves on purpose:

*Target selectors* (``Set*Target``) answer **where do I want to be**.  They are
one-tick actions that write ``target_x`` / ``target_y`` and nothing else, and
they are the *only* thing that differs between Blinky, Pinky, Inky and Clyde —
swapping one node in one XML file is what turns a chaser into an ambusher.

*Movers* (``StepTowardTarget``, ``WanderStep``, ``LeaveHouse``) answer **how do
I get one tile closer**.  They own the fiddly part — decision points, banned
reversals, the ghost-house door — so it is written exactly once.

That split is straight out of the arcade original, where a ghost's whole
personality is the tile it points at while every ghost moves by the same rule.
"""

from __future__ import annotations

from typing import List, Optional

from bteng import InputPort, NodeStatus, PortDefinition, register_node

from pacman_bteng.behaviors.nodes import (
    TARGET_INPUT_PORTS,
    GameNode,
    StepAction,
    TargetSelector,
    as_bool,
    as_float,
    as_int,
)
from pacman_bteng.game.entities import Ghost, GhostState
from pacman_bteng.game.geometry import Direction, Tile, euclidean
from pacman_bteng.game.pathfinding import (
    bfs_path,
    bfs_step,
    distance_field,
    legal_moves,
    order_by_distance,
    random_step,
    resolve_strategy,
)

from bteng import ActionNode


#: How far SetEscapeTarget bothers measuring.  Past this every candidate is
#: equally safe, and the openness bonus is what should break the tie anyway.
_ESCAPE_HORIZON = 14


def _heading(agent) -> Direction:
    """The direction an agent is facing, even while parked on a tile centre."""
    return agent.direction if agent.direction.is_moving else agent.last_direction


def _project_away(origin: Tile, threat: Tile, distance: float) -> Tile:
    """A tile *distance* tiles from *origin*, directly away from *threat*.

    Aiming at a point instead of picking a direction is what lets the flee
    behavior reuse the ordinary mover: "walk toward this tile" and "walk away
    from that ghost" become the same node.
    """
    dx = origin[0] - threat[0]
    dy = origin[1] - threat[1]
    length = (dx * dx + dy * dy) ** 0.5
    if length == 0.0:
        return origin
    return (
        int(round(origin[0] + dx / length * distance)),
        int(round(origin[1] + dy / length * distance)),
    )


# ═══════════════════════════════════════════════════════════════════════════
# TARGET SELECTORS — one per ghost personality
# ═══════════════════════════════════════════════════════════════════════════

@register_node()
class SetChaseTarget(TargetSelector):
    """Blinky. Aim straight at Pac-Man's tile — the shadow, relentless.

    The simplest possible target, and still the most dangerous one, because a
    greedy step toward Pac-Man's own tile never lets him gain ground in a
    straight corridor.
    """

    def choose(self) -> Optional[Tile]:
        target = self.world.pacman.tile
        self.report(f"chasing pacman at {target}")
        return target


@register_node()
class SetAmbushTarget(TargetSelector):
    """Pinky. Aim ``lead_tiles`` ahead of Pac-Man, not at him.

    Pointing at where he is *going* is what produces the pincer: Blinky pushes
    from behind while Pinky is already waiting at the next junction.
    """

    @classmethod
    def provided_ports(cls) -> List[PortDefinition]:
        return [
            *super().provided_ports(),
            InputPort("lead_tiles", "How far ahead of Pac-Man to aim", default=4.0),
        ]

    def choose(self) -> Optional[Tile]:
        pacman = self.world.pacman
        lead = as_int(self.get_input("lead_tiles", 4.0), 4)
        target = _heading(pacman).apply(pacman.tile, lead)
        self.report(f"ambushing {lead} tiles ahead at {target}")
        return target


@register_node()
class SetFlankTarget(TargetSelector):
    """Inky. Take the vector from ``partner`` to just ahead of Pac-Man, double it.

    The result swings wildly: harmless while the partner is far away, and
    brutal once Blinky has closed in, because the doubled vector then lands on
    the far side of Pac-Man and pins him between the two of them.
    """

    @classmethod
    def provided_ports(cls) -> List[PortDefinition]:
        return [
            *super().provided_ports(),
            InputPort("partner", "Ghost whose position anchors the vector", default="blinky"),
            InputPort("lead_tiles", "Pivot this far ahead of Pac-Man", default=2.0),
        ]

    def choose(self) -> Optional[Tile]:
        world = self.world
        partner_name = str(self.get_input("partner", "blinky"))
        partner: Optional[Ghost] = world.ghost(partner_name)
        pacman = world.pacman
        lead = as_int(self.get_input("lead_tiles", 2.0), 2)
        pivot = _heading(pacman).apply(pacman.tile, lead)

        if partner is None or partner is self.agent:
            # Nobody to flank with (a run with fewer ghosts): behave like Blinky
            # rather than failing the branch and freezing the ghost.
            self.report(f"no partner {partner_name!r}; chasing directly")
            return pacman.tile

        target = (2 * pivot[0] - partner.tile[0], 2 * pivot[1] - partner.tile[1])
        self.report(f"flanking off {partner_name} toward {target}")
        return target


@register_node()
class SetShyTarget(TargetSelector):
    """Clyde. Chase while far away, bolt for his corner once close.

    The oscillation is the point: he closes in, panics inside ``flee_radius``,
    retreats until he is outside it again, and comes back — which is why he
    reads as a coward rather than as a bad chaser.
    """

    @classmethod
    def provided_ports(cls) -> List[PortDefinition]:
        return [
            *super().provided_ports(),
            InputPort("flee_radius", "Closer than this, run home", default=8.0),
            InputPort("corner_x", "Retreat corner column (-1 = the ghost's own)", default=-1.0),
            InputPort("corner_y", "Retreat corner row (-1 = the ghost's own)", default=-1.0),
        ]

    def choose(self) -> Optional[Tile]:
        world = self.world
        radius = as_float(self.get_input("flee_radius", 8.0), 8.0)
        distance = euclidean(self.agent.tile, world.pacman.tile)
        if distance > radius:
            self.report(f"far enough ({distance:.1f} > {radius:g}) - chasing")
            return world.pacman.tile

        corner = _corner_from_ports(self)
        self.report(f"too close ({distance:.1f} <= {radius:g}) - retreating to {corner}")
        return corner


@register_node()
class SetScatterTarget(TargetSelector):
    """Retreat to this ghost's private corner during the scatter phases.

    With ``corner_x``/``corner_y`` left at -1 the ghost uses the corner the
    world assigned it, so the same XML works for any ghost.
    """

    @classmethod
    def provided_ports(cls) -> List[PortDefinition]:
        return [
            *super().provided_ports(),
            InputPort("corner_x", "Corner column (-1 = the ghost's own)", default=-1.0),
            InputPort("corner_y", "Corner row (-1 = the ghost's own)", default=-1.0),
        ]

    def choose(self) -> Optional[Tile]:
        corner = _corner_from_ports(self)
        self.report(f"scattering to {corner}")
        return corner


def _corner_from_ports(node: GameNode) -> Tile:
    """Corner from the XML ports, falling back to the ghost's assigned one."""
    x = as_int(node.get_input("corner_x", -1.0), -1)
    y = as_int(node.get_input("corner_y", -1.0), -1)
    if x >= 0 and y >= 0:
        return (x, y)
    ghost = node.ghost
    return ghost.scatter_corner if ghost is not None else node.agent.home


@register_node()
class SetFleeTarget(TargetSelector):
    """Frightened: aim at a point ``lookahead`` tiles directly away from Pac-Man.

    The arcade turns frightened ghosts at random instead; ``<WanderStep/>``
    reproduces that if you want it.  Projecting a run-away point keeps the
    behavior legible in the debug overlay — you can see what the ghost thinks
    safety looks like.
    """

    @classmethod
    def provided_ports(cls) -> List[PortDefinition]:
        return [
            *super().provided_ports(),
            InputPort("lookahead", "How far to project the escape point", default=10.0),
        ]

    def choose(self) -> Optional[Tile]:
        lookahead = as_float(self.get_input("lookahead", 10.0), 10.0)
        here = self.agent.tile
        pacman = self.world.pacman.tile
        target = _project_away(here, pacman, lookahead)
        if target == here:
            # Standing on him: any corner beats standing still.
            ghost = self.ghost
            target = ghost.scatter_corner if ghost is not None else self.agent.home
        self.report(f"fleeing toward {target}")
        return target


@register_node()
class SetHouseTarget(TargetSelector):
    """Eaten: aim at the middle of the ghost house — and revive on arrival.

    FAILURE once the eyes are home is not an error path, it is the handoff:
    the state flips to IN_HOUSE, this branch stops matching, and the
    ``ReactiveFallback`` falls straight through to the "leave the house" branch
    in the very same tick.
    """

    def choose(self) -> Optional[Tile]:
        world = self.world
        ghost = self.ghost
        if ghost is None:
            return None

        home = world.maze.house_centre()
        if ghost.tile == home or (world.maze.is_house(ghost.tile) and ghost.is_deciding):
            world.on_ghost_reached_house(ghost)
            self.report("home - reviving")
            return None

        self.report(f"eyes heading home to {home}")
        return home


# ═══════════════════════════════════════════════════════════════════════════
# TARGET SELECTORS — Pac-Man's autopilot
# ═══════════════════════════════════════════════════════════════════════════

@register_node()
class SetNearestPelletTarget(TargetSelector):
    """Aim at the closest pellet — by actual corridor distance, not by sight.

    Straight-line nearest is wrong often enough to matter: the pellet across
    the wall is two tiles away and thirty tiles of walking.  Ranking the
    ``candidates`` nearest by sight and then measuring each with a BFS keeps the
    cost bounded while getting the answer right.
    """

    @classmethod
    def provided_ports(cls) -> List[PortDefinition]:
        return [
            *super().provided_ports(),
            InputPort("candidates", "How many nearby pellets to path-check", default=10.0),
        ]

    def choose(self) -> Optional[Tile]:
        world = self.world
        pellets = world.pellet_tiles()
        if not pellets:
            self.report("board is clear")
            return None

        here = self.agent.tile
        shortlist = order_by_distance(pellets, here)[: max(1, as_int(self.get_input("candidates", 10.0), 10))]
        passable = self.passable()

        best, best_cost = shortlist[0], None
        for tile in shortlist:
            path = bfs_path(here, tile, passable, wrap=self.wrap)
            cost = len(path) if path else None
            if cost is not None and (best_cost is None or cost < best_cost):
                best, best_cost = tile, cost

        self.report(
            f"pellet at {best}"
            + (f" ({best_cost} tiles)" if best_cost is not None else " (straight line)")
        )
        return best


@register_node()
class SetHuntGhostTarget(TargetSelector):
    """Power pellet is burning: go and eat the nearest frightened ghost."""

    def choose(self) -> Optional[Tile]:
        world = self.world
        prey = world.edible_ghosts()
        if not prey:
            self.report("nothing edible")
            return None
        here = self.agent.tile
        target = min(prey, key=lambda g: euclidean(here, g.tile))
        self.report(f"hunting {target.name} at {target.tile}")
        return target.tile


@register_node()
class SetEscapeTarget(TargetSelector):
    """Threatened: step to the safest tile next to us.

    Projecting a point directly away from the nearest ghost is the obvious
    implementation and it is a trap — it walks straight into dead ends, because
    "away from him" and "somewhere I can leave again" are different questions.
    So this scores each neighbouring tile on two things instead:

    * how far the *closest* threat is from it (all hostile ghosts count, not
      just the nearest one — running from Blinky into Pinky is not an escape), and
    * how many exits it has, weighted by ``openness``.

    A corridor tile has two exits and a junction has three or four, so the
    bonus is what makes Pac-Man break for open ground rather than into a
    pocket.  Set ``openness="0"`` to get the naive behavior back and watch him
    die in corners.
    """

    @classmethod
    def provided_ports(cls) -> List[PortDefinition]:
        return [
            *super().provided_ports(),
            InputPort("openness", "Tiles of safety one extra exit is worth", default=0.6),
            InputPort("reverse_penalty", "Tiles of safety a U-turn must beat to win", default=0.0),
        ]

    def choose(self) -> Optional[Tile]:
        world = self.world
        here = self.agent.tile
        threats = [ghost.tile for ghost in world.hostile_ghosts()]
        if not threats:
            self.report("no threat")
            return None

        passable = self.passable()
        openness = as_float(self.get_input("openness", 0.6), 0.6)
        # Momentum: two neighbours that score nearly alike (a ghost on each
        # side) flip with every tile the ghosts move, and the agent paces
        # back and forth between them.  Charging a U-turn a few tiles of
        # safety keeps him going unless turning round is clearly better.
        reverse_penalty = as_float(self.get_input("reverse_penalty", 0.0), 0.0)
        behind = self.agent.last_direction.opposite

        # One distance field per threat, then every candidate tile is a lookup.
        # Corridor distance, not straight line: the whole point is to tell
        # "behind that wall" apart from "down this corridor".
        fields = [
            distance_field(threat, passable, wrap=self.wrap, max_distance=_ESCAPE_HORIZON)
            for threat in threats
        ]

        best: Optional[Tile] = None
        best_score = float("-inf")
        for direction in legal_moves(here, passable, wrap=self.wrap):
            tile = self.wrap(direction.apply(here))
            safety = min(float(field.get(tile, 99.0)) for field in fields)
            exits = len(legal_moves(tile, passable, wrap=self.wrap))
            score = safety + openness * exits
            if direction is behind:
                score -= reverse_penalty
            if score > best_score:
                best, best_score = tile, score

        if best is None:
            self.report("cornered")
            return None
        self.report(f"escaping to {best} (safety {best_score:.1f})")
        return best


# ═══════════════════════════════════════════════════════════════════════════
# MOVERS — shared by every agent
# ═══════════════════════════════════════════════════════════════════════════

@register_node()
class StepTowardTarget(StepAction):
    """Take one tile of travel toward ``target_x`` / ``target_y``.

    ``pathfinder`` picks the navigator:

    ``greedy`` (default)
        The arcade rule — nearest neighbour by straight-line distance, no
        lookahead, no reversing.  Beatable around corners, which is what makes
        the game a game.
    ``bfs``
        A real shortest path.  Used by the eyes going home, where getting stuck
        would be a bug rather than a personality.
    """

    @classmethod
    def provided_ports(cls) -> List[PortDefinition]:
        return [
            *super().provided_ports(),
            *TARGET_INPUT_PORTS,
            InputPort("pathfinder", "greedy | bfs", default="greedy"),
        ]

    def choose_direction(self) -> Direction:
        target = self.read_target()
        name = str(self.get_input("pathfinder", "greedy"))
        strategy = resolve_strategy(name)
        allow_reverse = as_bool(self.get_input("allow_reverse", "false"), False)

        direction = strategy(
            self.agent.tile,
            target,
            self.passable(),
            forbidden=self.forbidden_direction(allow_reverse),
            wrap=self.wrap,
        )
        if direction.is_moving:
            self.report(f"{direction.name} toward {target} [{name}]")
        return direction


@register_node()
class WanderStep(StepAction):
    """Take one tile in a random legal direction.

    Two jobs: it is the authentic frightened-ghost behavior, and it is the
    safety net at the bottom of every tree — a ghost whose branches all failed
    keeps moving instead of standing in a corridor looking broken.
    """

    def choose_direction(self) -> Direction:
        direction = random_step(
            self.agent.tile,
            self.passable(),
            forbidden=self.forbidden_direction(
                as_bool(self.get_input("allow_reverse", "false"), False)
            ),
            wrap=self.wrap,
            rng=self.world.rng,
        )
        if direction.is_moving:
            self.report(f"wandering {direction.name}")
        return direction


@register_node()
class LeaveHouse(GameNode, ActionNode):
    """Wait out the release timer, then walk out through the door.

    Deliberately *not* a :class:`StepAction`: this node stays RUNNING across
    several tiles (and across the whole wait in the pen), because "leaving the
    house" is one behavior from the tree's point of view even though it takes
    half a dozen decision points to finish.  It is also the only node allowed
    to hand a ghost back to the world as released.
    """

    @classmethod
    def provided_ports(cls) -> List[PortDefinition]:
        return []

    def tick(self) -> NodeStatus:
        world = self.world
        ghost = self.ghost
        if ghost is None:
            return NodeStatus.FAILURE

        if ghost.state is GhostState.IN_HOUSE:
            self.report(f"waiting in the pen ({max(0.0, ghost.release_timer):.1f}s)")
            return NodeStatus.RUNNING

        exit_tile = world.maze.door_exit()
        ghost.target = exit_tile

        if ghost.tile == exit_tile:
            world.on_ghost_left_house(ghost)
            self.report("out of the house")
            return NodeStatus.SUCCESS

        if ghost.is_deciding:
            direction = bfs_step(
                ghost.tile,
                exit_tile,
                self.passable(),          # may_pass_door is True while LEAVING
                wrap=self.wrap,
            )
            if not direction.is_moving:
                self.report("door is blocked")
                return NodeStatus.FAILURE
            world.request_direction(ghost, direction)
            self.report(f"leaving via {direction.name}")

        return NodeStatus.RUNNING
