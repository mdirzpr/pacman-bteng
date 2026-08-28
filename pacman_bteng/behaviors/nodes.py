"""Shared plumbing for every Pac-Man behavior-tree node.

BTEng nodes are constructed by the XML parser with nothing but a name and a
:class:`~bteng.NodeConfig`, so they cannot be handed the game objects at build
time.  They pick them up from the blackboard instead, and this module is the one
place that knows how.

Three BTEng rules the whole node library obeys — deviate and the tree either
fails validation at start-up or misbehaves silently:

1. **Static XML attributes arrive as strings.**  ``distance="6"`` reads back as
   ``"6"``, so every numeric port goes through :func:`as_float` / :func:`as_int`.
2. **An input port whose ``default`` is ``None`` is REQUIRED.**  Give every
   input a real default unless you genuinely want start-up to fail when the XML
   omits it.
3. **``<TreeNodesModel>`` directions must match :meth:`provided_ports`.**  The
   parser routes ``attr="{key}"`` to the input or the output map based on the
   model; disagree with the node and ``Tree.validate()`` raises.
"""

from __future__ import annotations

from typing import Any, Callable, List, Optional, Sequence

from bteng import (
    ActionNode,
    ConditionNode,
    InputPort,
    NodeStatus,
    OutputPort,
    PortDefinition,
    StatefulActionNode,
)

from pacman_bteng.behaviors import blackboard_keys as K
from pacman_bteng.game.entities import Agent, Ghost
from pacman_bteng.game.geometry import Direction, Tile
from pacman_bteng.game.world import GameWorld

#: Ports every target-selecting action shares.  Declared as OUTPUTS: the setter
#: writes the tile it picked, ``StepTowardTarget`` reads it back as an INPUT.
TARGET_OUTPUT_PORTS: List[PortDefinition] = [
    OutputPort(K.TARGET_X, "Column of the tile this agent is heading for"),
    OutputPort(K.TARGET_Y, "Row of the tile this agent is heading for"),
]

TARGET_INPUT_PORTS: List[PortDefinition] = [
    InputPort(K.TARGET_X, "Column of the tile to walk to", default=0.0),
    InputPort(K.TARGET_Y, "Row of the tile to walk to", default=0.0),
]


# ── Attribute casting ─────────────────────────────────────────────────────────

def as_float(value: Any, fallback: float = 0.0) -> float:
    """Cast an XML attribute (always a string) to float, tolerantly."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def as_int(value: Any, fallback: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return fallback


def as_bool(value: Any, fallback: bool = False) -> bool:
    """``true/yes/on/1`` → True.  XML has no booleans, only strings."""
    if isinstance(value, bool):
        return value
    if value is None:
        return fallback
    return str(value).strip().lower() in ("1", "true", "yes", "on")


# ── The mixin every node in this package uses ─────────────────────────────────

class GameNode:
    """Blackboard-backed access to the game, plus the small helpers on top.

    Mixed in *before* the BTEng base class so the cooperative ``__init__``
    chain still reaches ``TreeNode``.
    """

    # ── Live objects ──────────────────────────────────────────────────────────

    @property
    def world(self) -> GameWorld:
        world = self.blackboard.get(K.WORLD) if self.blackboard else None
        if world is None:
            raise RuntimeError(
                f"{type(self).__name__} {self.name!r}: no {K.WORLD!r} on the "
                "blackboard. Behavior trees must be built through "
                "pacman_bteng.behaviors.AgentBrain, which seeds it."
            )
        return world

    @property
    def agent(self) -> Agent:
        agent = self.blackboard.get(K.AGENT) if self.blackboard else None
        if agent is None:
            raise RuntimeError(
                f"{type(self).__name__} {self.name!r}: no {K.AGENT!r} on the blackboard."
            )
        return agent

    @property
    def ghost(self) -> Optional[Ghost]:
        """The agent as a Ghost, or None when this tree drives Pac-Man.

        Every node in the library works for both, so ghost-only bookkeeping
        (``target``, ``behavior``) is always guarded by this.
        """
        agent = self.agent
        return agent if isinstance(agent, Ghost) else None

    # ── Maze helpers ──────────────────────────────────────────────────────────

    def passable(self) -> Callable[[Tile], bool]:
        """Tile predicate for *this* agent — the door is the only difference."""
        return self.world.passable_for(self.agent)

    def wrap(self, tile: Tile) -> Tile:
        return self.world.wrap(tile)

    def clamp(self, tile: Tile) -> Tile:
        """Pull a tile back inside the maze rectangle.

        Target tiles are allowed to be imaginary — the arcade ghosts aim at
        points outside the maze all the time — but a target that is thousands of
        tiles away makes the greedy step numerically pointless, so extrapolated
        targets get clamped to the board.
        """
        maze = self.world.maze
        x, y = tile
        return (max(0, min(maze.width - 1, int(x))), max(0, min(maze.height - 1, int(y))))

    def forbidden_direction(self, allow_reverse: bool = False) -> Direction:
        """The reversal this agent may not make.

        Ghosts never turn back on themselves except when the world explicitly
        flips them (a mode change or a power pellet), which it does by rewriting
        ``last_direction`` — so honouring it here is all that is needed.
        """
        if allow_reverse:
            return Direction.NONE
        return self.agent.last_direction.opposite

    # ── Target ports ──────────────────────────────────────────────────────────

    def write_target(self, tile: Tile) -> Tile:
        """Publish the chosen target on the blackboard and return it.

        Also stashed on the ghost so the debug overlay can draw the line the
        tree is actually steering along — seeing that line is most of the value
        of a behavior tree you can watch.
        """
        tile = (int(tile[0]), int(tile[1]))
        self.set_output(K.TARGET_X, float(tile[0]))
        self.set_output(K.TARGET_Y, float(tile[1]))
        self.agent.target = tile
        return tile

    def read_target(self) -> Tile:
        return (
            as_int(self.get_input(K.TARGET_X, 0.0)),
            as_int(self.get_input(K.TARGET_Y, 0.0)),
        )

    # ── Reporting ─────────────────────────────────────────────────────────────

    def report(self, message: str) -> None:
        """Attach a human-readable reason to this tick.

        It reaches three places at once: the Inspector record, the ZMQ stream
        (``reason`` field) and, for ghosts, the GUI side panel.
        """
        self.set_feedback_message(message)
        ghost = self.ghost
        if ghost is not None:
            ghost.behavior = message


# ── Convenience bases ─────────────────────────────────────────────────────────

class GameCondition(GameNode, ConditionNode):
    """A side-effect-free predicate over the world.

    Conditions are the reactive part of the tree: ``ReactiveFallback``
    re-evaluates every one of them on every tick, no matter what a lower
    priority branch is doing.
    """

    @classmethod
    def provided_ports(cls) -> List[PortDefinition]:
        return []

    def verdict(self, ok: bool, why: str = "") -> NodeStatus:
        if why:
            self.report(why)
        return NodeStatus.SUCCESS if ok else NodeStatus.FAILURE


class TargetSelector(GameNode, ActionNode):
    """An action whose whole job is to write ``target_x`` / ``target_y``.

    Splitting "where do I want to go" from "take a step" is what makes the four
    ghosts differ by one node in one XML file, and it keeps the movement code —
    the fiddly part — written exactly once.
    """

    @classmethod
    def provided_ports(cls) -> List[PortDefinition]:
        return list(TARGET_OUTPUT_PORTS)

    def choose(self) -> Optional[Tile]:
        """Return the tile to aim at, or None to fail this branch."""
        raise NotImplementedError(f"{type(self).__name__}.choose() not implemented")

    def tick(self) -> NodeStatus:
        tile = self.choose()
        if tile is None:
            return NodeStatus.FAILURE
        self.write_target(self.clamp(tile))
        return NodeStatus.SUCCESS


class StepAction(GameNode, StatefulActionNode):
    """Commit the agent to one tile of travel, then wait for it to arrive.

    THE THREE-PHASE SHAPE
    ---------------------
    ``on_start``   pick a direction and hand it to the world — RUNNING
    ``on_running`` still sliding? RUNNING.  Arrived? SUCCESS
    ``on_halted``  drop the commitment so the next activation re-decides

    Returning RUNNING for the length of a tile is deliberate: it is what makes
    the tree's state mean something in the live viewer, and it is what lets a
    higher-priority branch (a power pellet going off) interrupt a ghost
    mid-corridor through ``ReactiveFallback``.
    """

    @classmethod
    def provided_ports(cls) -> List[PortDefinition]:
        return [
            InputPort("allow_reverse", "Let the agent turn back on itself", default="false"),
        ]

    def __init__(self, name: str, config=None) -> None:
        super().__init__(name, config)
        self._committed = False

    # ── Subclass hook ─────────────────────────────────────────────────────────

    def choose_direction(self) -> Direction:
        """Which way out of the current tile?  ``NONE`` fails the branch."""
        raise NotImplementedError(
            f"{type(self).__name__}.choose_direction() not implemented"
        )

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def on_start(self) -> NodeStatus:
        agent = self.agent
        # Mid-tile: a direction can only be taken at a tile centre, so stay
        # RUNNING and try again next tick rather than failing the branch.
        if not agent.is_deciding:
            self._committed = False
            self.report("in transit")
            return NodeStatus.RUNNING

        direction = self.choose_direction()
        if not direction.is_moving:
            self.report("boxed in")
            return NodeStatus.FAILURE

        if not self.world.request_direction(agent, direction):
            self.report(f"{direction.name} blocked")
            return NodeStatus.FAILURE

        self._committed = True
        return NodeStatus.RUNNING

    def on_running(self) -> NodeStatus:
        if not self._committed:
            return self.on_start()
        if self.agent.is_deciding:      # the world cleared it: a tile was reached
            self._committed = False
            return NodeStatus.SUCCESS
        return NodeStatus.RUNNING

    def on_halted(self) -> None:
        self._committed = False

    def _on_reset(self) -> None:
        self._committed = False


def first_or_none(items: Sequence[Any]) -> Optional[Any]:
    return items[0] if items else None
