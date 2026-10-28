"""Condition nodes — the guards that decide which branch a ghost is in.

These are the reactive half of every tree.  ``ReactiveFallback`` re-ticks each
of them on every tick, so a power pellet going off, a mode switch or Pac-Man
walking round a corner interrupts whatever the ghost was doing in the same
frame it happens.  Keep them cheap and free of side effects.

Registered with BTEng's global ``NodeFactory`` by ``@register_node()``, so the
XML addresses each one by tag name (``<IsFrightened/>``) with no ``ID=``.
"""

from __future__ import annotations

from typing import List

from bteng import InputPort, NodeStatus, PortDefinition, register_node

from pacman_bteng.behaviors.nodes import GameCondition, as_bool, as_float
from pacman_bteng.game.entities import GhostState
from pacman_bteng.game.geometry import euclidean, manhattan
from pacman_bteng.game.pathfinding import distance_field
from pacman_bteng.game.world import GlobalMode



def _state_of(ghost) -> str:
    """The observation the ghost-state conditions report.

    Neutral on purpose: the node status already carries the verdict, so the
    feedback message is more useful saying what the state actually IS than
    restating the question.
    """
    return f"state={ghost.state.value}" if ghost is not None else "not a ghost"
# ── Ghost life cycle ──────────────────────────────────────────────────────────

@register_node()
class IsEaten(GameCondition):
    """SUCCESS while this ghost is a pair of eyes heading home."""

    def tick(self) -> NodeStatus:
        ghost = self.ghost
        return self.verdict(
            ghost is not None and ghost.state is GhostState.EATEN,
            _state_of(ghost),
        )


@register_node()
class IsInHouse(GameCondition):
    """SUCCESS while the ghost is still in the pen, or walking out of it.

    Both states are one branch on purpose: the ghost that is waiting and the
    ghost that is squeezing through the door want the same exit behavior, one
    of them just has not been released yet.
    """

    def tick(self) -> NodeStatus:
        ghost = self.ghost
        return self.verdict(
            ghost is not None
            and ghost.state in (GhostState.IN_HOUSE, GhostState.LEAVING),
            _state_of(ghost),
        )


@register_node()
class IsFrightened(GameCondition):
    """SUCCESS while a power pellet has this ghost running scared."""

    def tick(self) -> NodeStatus:
        ghost = self.ghost
        return self.verdict(
            ghost is not None and ghost.state is GhostState.FRIGHTENED,
            _state_of(ghost),
        )


# ── Global game state ─────────────────────────────────────────────────────────

@register_node()
class IsScatterMode(GameCondition):
    """SUCCESS during the scatter phases of the level's mode schedule."""

    def tick(self) -> NodeStatus:
        world = self.world
        return self.verdict(world.mode is GlobalMode.SCATTER, f"mode={world.mode.value}")


@register_node()
class IsPowerActive(GameCondition):
    """SUCCESS while a power pellet is burning down (Pac-Man's tree uses it)."""

    def tick(self) -> NodeStatus:
        world = self.world
        return self.verdict(world.power_active, f"power {world.power_timer:.1f}s")


@register_node()
class HasPelletsLeft(GameCondition):
    """SUCCESS while anything is still on the board."""

    def tick(self) -> NodeStatus:
        left = self.world.pellets_left
        return self.verdict(left > 0, f"{left} pellets left")



# ── Distance metrics ──────────────────────────────────────────────────────────

#: Stand-in for "unreachable".  Larger than any radius a tree would sensibly
#: ask about, and finite so it still compares cleanly.
UNREACHABLE = 999.0


class _ProximityCondition(GameCondition):
    """Shared distance measuring for the two "is X within N tiles" conditions.

    ``metric="path"`` is the honest one in a maze: it walks the corridors, so a
    ghost on the other side of a wall does not read as close.  It costs one BFS
    over the reachable board per tick, which at four ghosts and sixty ticks a
    second is still nothing — and it is the difference between an autopilot that
    dodges and one that walks into a pincer.
    """

    def _field(self, metric: str, radius: float = 0.0):
        """The corridor-distance field, bounded by the radius being tested."""
        if metric != "path":
            return None
        return distance_field(
            self.agent.tile,
            self.passable(),
            wrap=self.wrap,
            max_distance=int(radius) + 1 if radius else None,
        )

    def _measure(self, metric: str, here, there, field=None) -> float:
        if metric == "manhattan":
            return float(manhattan(here, there))
        if metric == "path":
            if field is None:
                field = self._field(metric, radius)
            return float(field.get(self.wrap(there), UNREACHABLE))
        return euclidean(here, there)
# ── Proximity ─────────────────────────────────────────────────────────────────

@register_node()
class IsPacmanWithin(_ProximityCondition):
    """SUCCESS when Pac-Man is closer than ``distance`` tiles.

    ``metric="manhattan"`` measures along the corridors instead of through the
    walls — a better proxy for "can he reach me" in a maze, and the reason the
    port exists at all.
    """

    @classmethod
    def provided_ports(cls) -> List[PortDefinition]:
        return [
            InputPort("distance", "Trigger radius in tiles", default=8.0),
            InputPort("metric", "euclidean | manhattan | path", default="euclidean"),
        ]

    def tick(self) -> NodeStatus:
        radius = as_float(self.get_input("distance", 8.0), 8.0)
        metric = str(self.get_input("metric", "euclidean")).lower()
        here = self.agent.tile
        there = self.world.pacman.tile
        distance = self._measure(metric, here, there, self._field(metric, radius))
        return self.verdict(
            distance <= radius, f"pacman {distance:.1f} tiles (limit {radius:g}, {metric})"
        )


@register_node()
class IsGhostWithin(_ProximityCondition):
    """SUCCESS when a ghost is closer than ``distance`` tiles.

    Used by Pac-Man's autopilot from both sides: ``hostile_only="true"`` asks
    "am I in danger", ``hostile_only="false"`` asks "is there a meal nearby".
    """

    @classmethod
    def provided_ports(cls) -> List[PortDefinition]:
        return [
            InputPort("distance", "Trigger radius in tiles", default=6.0),
            InputPort("hostile_only", "Ignore edible and eaten ghosts", default="true"),
            InputPort("edible_only", "Only count ghosts that can be eaten", default="false"),
            InputPort("metric", "euclidean | manhattan | path", default="euclidean"),
            InputPort("hysteresis", "Extra tiles a firing guard keeps holding for", default=0.0),
        ]

    def __init__(self, name: str, config=None) -> None:
        super().__init__(name, config)
        # Whether the last tick said yes.  Survives reset_tree() on purpose:
        # remembering that we were already alarmed is the whole point.
        self._latched = False

    def tick(self) -> NodeStatus:
        radius = as_float(self.get_input("distance", 6.0), 6.0)
        hysteresis = max(0.0, as_float(self.get_input("hysteresis", 0.0), 0.0))
        hostile_only = as_bool(self.get_input("hostile_only", "true"), True)
        edible_only = as_bool(self.get_input("edible_only", "false"), False)
        metric = str(self.get_input("metric", "euclidean")).lower()
        world = self.world
        here = self.agent.tile

        if edible_only:
            pool = world.edible_ghosts()
        elif hostile_only:
            pool = world.hostile_ghosts()
        else:
            pool = list(world.ghosts)

        if not pool:
            self._latched = False
            return self.verdict(False, "no ghost of that kind on the board")

        # One field, every ghost measured against it — see _measure.
        field = self._field(metric)
        nearest = min(pool, key=lambda g: self._measure(metric, here, g.tile, field))
        distance = self._measure(metric, here, nearest.tile, field)

        # Schmitt trigger: fire inside `radius`, but once firing hold until the
        # ghost is `radius + hysteresis` away.  A single threshold flips every
        # tile for a ghost hovering at the edge, and a guard that flips makes
        # the branch below it turn the agent round and back again.
        limit = radius + hysteresis if self._latched else radius
        self._latched = distance <= limit
        return self.verdict(
            self._latched,
            f"{nearest.name} {distance:.1f} tiles (limit {limit:g}, {metric})",
        )
