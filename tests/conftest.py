"""Shared fixtures and helpers for the pacman-bteng test suite.

THREE THINGS EVERY TEST HERE OBEYS
----------------------------------
1. **No window, no socket, no sleeping.**  ``BrainPool`` is built with
   ``introspection=None`` and the ``settings`` fixture forces
   ``enable_zmq = False``, so nothing in this suite ever binds a port or opens
   an SDL display.  A test that needs a ZMQ publisher does not belong here.
2. **Always seeded.**  ``WanderStep`` draws from ``world.rng``, so an unseeded
   world makes the integration test flap.  Every world fixture takes a seed.
3. **The world starts in READY.**  ``GameWorld.update`` refuses to move anything
   until the countdown expires, which would silently turn most of
   ``test_world.py`` into a no-op — hence :func:`start_playing`.
"""

from __future__ import annotations

from typing import Any, Dict, Iterator, Optional, Type

import pytest
from bteng import Blackboard, NodeConfig, NodeStatus

from pacman_bteng.behaviors import BrainPool
from pacman_bteng.behaviors import blackboard_keys as K
from pacman_bteng.config import PROJECT_ROOT, Settings
from pacman_bteng.game.entities import Agent
from pacman_bteng.game.maze import Maze
from pacman_bteng.game.world import GamePhase, GameWorld

#: The maze the game actually ships with — the tests assert against the real
#: asset, not a hand-rolled stand-in, because the map is content that people
#: edit and a broken edit is exactly what this suite has to catch.
CLASSIC_MAZE = PROJECT_ROOT / "assets" / "mazes" / "classic.txt"
BT_DIR = PROJECT_ROOT / "bt_structures"

#: One seed for the whole suite.  Changing it is allowed; leaving it unset is
#: not, because the autopilot integration test would then pass on some runs.
SEED = 1234


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def maze() -> Maze:
    """The real ``assets/mazes/classic.txt``.

    Session-scoped and safe to share: ``Maze`` is a frozen dataclass holding
    only immutable collections, so no test can contaminate another through it.
    """
    return Maze.from_file(CLASSIC_MAZE)


@pytest.fixture
def settings() -> Settings:
    """``Settings.load()`` on the real config, with the ZMQ stream forced off.

    Loading the shipped YAML rather than the dataclass defaults is deliberate:
    it means a rules change that only lands in the file (speeds, the mode
    schedule) is exercised by the same tests that exercise the code.
    """
    loaded = Settings.load()
    loaded.introspection.enable_zmq = False
    loaded.introspection.console_log = False
    loaded.introspection.log_file = None
    loaded.seed = SEED
    return loaded


@pytest.fixture
def world(maze: Maze, settings: Settings) -> GameWorld:
    """A fresh, seeded game on the classic maze, still in the READY phase."""
    return GameWorld(
        maze,
        settings.rules,
        ghost_names=settings.ghosts,
        level=settings.level,
        seed=SEED,
    )


@pytest.fixture
def brains(world: GameWorld, settings: Settings) -> Iterator[BrainPool]:
    """A ``BrainPool`` for the four ghosts, with introspection disabled.

    Torn down explicitly: every ``AgentBrain`` owns a ``TreeExecutor``, and
    leaking those across a few hundred tests is how a suite ends up mysteriously
    slow.
    """
    pool = BrainPool(world, settings, introspection=None)
    try:
        yield pool
    finally:
        pool.shutdown()


# ── Helpers (imported by the test modules) ────────────────────────────────────

def start_playing(world: GameWorld) -> GameWorld:
    """Skip the READY countdown so ``update()`` actually simulates.

    Setting the phase directly rather than feeding in ``ready_duration``
    seconds keeps the tests independent of that tunable — and of the two tiles
    of travel a ghost would otherwise have made before the assertions start.
    """
    world.phase = GamePhase.PLAYING
    world.phase_timer = 0.0
    return world


def seeded_blackboard(agent: Agent, world: GameWorld) -> Blackboard:
    """A private blackboard seeded exactly the way ``AgentBrain`` seeds one.

    Node tests build their nodes by hand instead of through a tree, so this is
    the one place that has to stay in step with the runner.
    """
    board = Blackboard(scope_name=f"test.{agent.name}")
    K.seed(board, agent, world)
    return board


#: The two ports every target selector writes and every mover reads.  The XML
#: wires them with ``target_x="{target_x}"``; a hand-built node has to say so.
TARGET_PORTS = {K.TARGET_X: K.TARGET_X, K.TARGET_Y: K.TARGET_Y}


def make_node(
    node_cls: Type[Any],
    board: Blackboard,
    *,
    params: Optional[Dict[str, Any]] = None,
    inputs: Optional[Dict[str, str]] = None,
    outputs: Optional[Dict[str, str]] = None,
    name: str = "UnderTest",
):
    """Build one BT node outside any tree, wired the way the XML parser wires it.

    ``params`` are the *static* XML attributes — and they are passed as strings
    on purpose, because that is the only form a node ever sees in production
    (``distance="6"`` is the string ``"6"``, never the number 6).
    """
    config = NodeConfig(
        blackboard=board,
        input_ports=dict(inputs or {}),
        output_ports=dict(outputs or {}),
        params=dict(params or {}),
    )
    return node_cls(name, config)


def tick(node) -> NodeStatus:
    """Tick a node through ``execute_tick`` so its status bookkeeping is real.

    Calling ``node.tick()`` directly would skip the status write that
    ``StatefulActionNode`` reads to decide between ``on_start`` and
    ``on_running`` — which is precisely the behavior the mover tests are about.
    """
    return node.execute_tick()


def run_headless(
    world: GameWorld,
    pool: BrainPool,
    seconds: float,
    *,
    dt: float = 1.0 / 60.0,
    on_step=None,
) -> int:
    """Play *seconds* of game time with no GUI. Returns the number of frames.

    The tick order mirrors ``PacmanApp._step`` exactly — trees first, world
    second — because a test that ran them the other way round would be
    measuring a game nobody plays.
    """
    frames = int(seconds / dt)
    for _ in range(frames):
        if world.phase is GamePhase.PLAYING and not world.paused:
            pool.tick_all(dt)
        world.update(dt)
        if on_step is not None:
            on_step()
    return frames
