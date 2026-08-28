"""The blackboard contract between the game and the behavior trees.

Every tree gets its *own* blackboard, scoped to one agent, and the game pushes
the world state into it once per tick (:func:`sync`).  Two kinds of entries live
there and they serve different purposes:

``agent`` / ``world``
    Live object references.  The node library reads the real game objects
    through these — a maze lookup or a BFS is not something you want to squeeze
    through a scalar port.

everything else
    Flat scalars that mirror the same state.  Nothing in the node library
    *needs* them, but they are what shows up in the ZmqPublisher stream and the
    live viewers, so a mirrored key is the difference between "SelectTarget
    RUNNING" and actually being able to see why.  They also mark the blackboard
    dirty on every tick, which is what keeps ``ReactiveFallback`` fully
    reactive instead of taking its skip-ahead fast path.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    from bteng import Blackboard

    from pacman_bteng.game.entities import Agent
    from pacman_bteng.game.world import GameWorld

# ── Object references (set once, at brain construction) ───────────────────────

AGENT = "agent"
WORLD = "world"

# ── Ports written by the target-selecting actions, read by the movers ─────────

TARGET_X = "target_x"
TARGET_Y = "target_y"

# ── Mirrors, refreshed every tick by sync() ───────────────────────────────────

AGENT_NAME = "agent_name"
AGENT_X = "agent_x"
AGENT_Y = "agent_y"
AGENT_STATE = "agent_state"
AGENT_DIRECTION = "agent_direction"
PACMAN_X = "pacman_x"
PACMAN_Y = "pacman_y"
DISTANCE_TO_PACMAN = "distance_to_pacman"
MODE = "mode"
POWER_REMAINING = "power_remaining"
PELLETS_LEFT = "pellets_left"
LEVEL = "level"

#: Keys the GUI panel and the viewers are expected to find, in display order.
MIRRORED = (
    AGENT_NAME,
    AGENT_STATE,
    AGENT_X,
    AGENT_Y,
    AGENT_DIRECTION,
    PACMAN_X,
    PACMAN_Y,
    DISTANCE_TO_PACMAN,
    MODE,
    POWER_REMAINING,
    PELLETS_LEFT,
    LEVEL,
)


def seed(blackboard: "Blackboard", agent: "Agent", world: "GameWorld") -> None:
    """Install the object references. Called once, when the brain is built."""
    blackboard.set(AGENT, agent)
    blackboard.set(WORLD, world)
    blackboard.set(TARGET_X, float(agent.tile[0]))
    blackboard.set(TARGET_Y, float(agent.tile[1]))
    sync(blackboard, agent, world)


def sync(blackboard: "Blackboard", agent: "Agent", world: "GameWorld") -> None:
    """Refresh the scalar mirrors from the world. Called once per tick."""
    state = getattr(agent, "state", None)
    blackboard.set(AGENT_NAME, agent.name)
    blackboard.set(AGENT_STATE, state.value if state is not None else "player")
    blackboard.set(AGENT_X, float(agent.tile[0]))
    blackboard.set(AGENT_Y, float(agent.tile[1]))
    blackboard.set(AGENT_DIRECTION, agent.direction.name)
    blackboard.set(PACMAN_X, float(world.pacman.tile[0]))
    blackboard.set(PACMAN_Y, float(world.pacman.tile[1]))
    blackboard.set(DISTANCE_TO_PACMAN, round(world.distance_to_pacman(agent.tile), 2))
    blackboard.set(MODE, world.mode.value)
    blackboard.set(POWER_REMAINING, round(world.power_timer, 2))
    blackboard.set(PELLETS_LEFT, world.pellets_left)
    blackboard.set(LEVEL, world.level)
