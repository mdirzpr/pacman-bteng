"""The XML trees: they parse, they validate, and they actually play the game.

Two layers here, and the second is the one that earns its keep:

* every file loads through ``AgentBrain`` and passes ``Tree.validate()`` — the
  real regression test for the ``<TreeNodesModel>`` port directions, which are
  the one thing in this project you can get wrong without any symptom until
  start-up; and
* a headless run of the whole game with all five brains attached, asserting
  that it makes progress.  A tree can validate perfectly and still be a ghost
  standing still in a corridor.
"""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
from bteng import NodeStatus
from conftest import BT_DIR, SEED, run_headless, start_playing

from pacman_bteng.behaviors import AgentBrain, BrainPool, registered_nodes
from pacman_bteng.config import GHOST_NAMES, Settings
from pacman_bteng.game.entities import GhostState
from pacman_bteng.game.maze import Maze
from pacman_bteng.game.world import GamePhase, GameWorld

TREE_FILES = sorted(BT_DIR.glob("*.xml"))
BTPROJ = BT_DIR / "pacman.btproj"

#: ``<!-- ... -->`` with the body captured. Non-greedy so two comments in a row
#: are two matches rather than one that swallows the code between them.
_COMMENT = re.compile(r"<!--(.*?)-->", re.DOTALL)


def test_there_is_a_tree_for_every_agent() -> None:
    names = {path.name for path in TREE_FILES}
    assert names == {f"ghost_{ghost}.xml" for ghost in GHOST_NAMES} | {"pacman_auto.xml"}


# ── Well-formedness ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("path", TREE_FILES, ids=lambda p: p.name)
def test_the_file_is_well_formed_xml(path: Path) -> None:
    root = ET.parse(path).getroot()
    assert root.tag == "BTEng"
    assert root.get("main_tree_to_execute")
    assert len(root.findall("Tree")) == 1
    assert root.find("TreeNodesModel") is not None


@pytest.mark.parametrize("path", TREE_FILES, ids=lambda p: p.name)
def test_no_double_hyphen_inside_a_comment(path: Path) -> None:
    """``--`` is illegal inside an XML comment, and it has bitten us once.

    An em-dash typed as ``--`` in a banner comment makes the whole file
    unparseable, and the failure surfaces as a BrainError at start-up with the
    parser's own message — a long way from the line that caused it.  Checking
    it here names the file and the text.
    """
    for body in _COMMENT.findall(path.read_text(encoding="utf-8")):
        assert "--" not in body, f"{path.name}: '--' inside a comment: {body[:60]!r}"
        assert not body.endswith("-"), f"{path.name}: comment ends in '-'"


@pytest.mark.parametrize("path", TREE_FILES, ids=lambda p: p.name)
def test_every_node_instance_is_named_and_prefixed(path: Path) -> None:
    # The five trees share one ZMQ stream; the "Blinky/" prefix is what keeps it
    # readable and what ./run.sh viewer --filter keys off.
    tree = ET.parse(path).find("Tree")
    prefixes = set()
    for node in tree.iter():
        if node is tree:
            continue
        name = node.get("name")
        assert name, f"{path.name}: <{node.tag}> has no name="
        assert "/" in name, f"{path.name}: {name!r} is not agent-prefixed"
        prefixes.add(name.split("/", 1)[0])
    assert len(prefixes) == 1, f"{path.name}: mixed prefixes {prefixes}"


# ── The project index ─────────────────────────────────────────────────────────

def test_the_btproj_lists_exactly_the_files_that_exist() -> None:
    project = json.loads(BTPROJ.read_text(encoding="utf-8"))
    listed = set(project["xml_files"])

    assert listed == {path.name for path in TREE_FILES}
    for name in listed:
        assert (BT_DIR / name).exists(), f"{name} is listed but missing"


def test_the_btproj_maps_every_agent_to_a_listed_file() -> None:
    project = json.loads(BTPROJ.read_text(encoding="utf-8"))
    assert set(project["agents"]) == set(GHOST_NAMES) | {"pacman"}
    for agent, name in project["agents"].items():
        assert name in project["xml_files"], f"{agent} points at an unlisted file"


# ── Loading and validation ────────────────────────────────────────────────────

@pytest.mark.parametrize("path", TREE_FILES, ids=lambda p: p.name)
def test_the_tree_loads_and_validates(path: Path, world: GameWorld) -> None:
    """``Tree.validate()`` is what catches a wrong port direction.

    The XML parser routes ``target_x="{target_x}"`` to the node's input map or
    its output map based on the ``<TreeNodesModel>`` entry alone.  Get the
    direction wrong and the node silently reads a port nobody writes — the
    ghost aims at (0, 0) forever and nothing raises.  Validation compares the
    model against ``provided_ports()`` and refuses.
    """
    agent = world.pacman if path.stem == "pacman_auto" else world.ghost(path.stem.split("_")[1])
    brain = AgentBrain(agent, world, path)
    try:
        brain.tree.validate()
        assert brain.status is NodeStatus.IDLE
        assert brain.tick() in (NodeStatus.RUNNING, NodeStatus.SUCCESS, NodeStatus.FAILURE)
    finally:
        brain.shutdown()


@pytest.mark.parametrize("path", TREE_FILES, ids=lambda p: p.name)
def test_every_custom_tag_used_is_actually_registered(path: Path) -> None:
    # A typo'd tag is a parse error at start-up; naming it here is friendlier.
    known = set(registered_nodes())
    control = {"Sequence", "Fallback", "ReactiveFallback", "ReactiveSequence", "Parallel"}
    tree = ET.parse(path).find("Tree")
    for node in tree.iter():
        if node is tree or node.tag in control:
            continue
        assert node.tag in known, f"{path.name}: <{node.tag}> is not a registered node"


def test_a_missing_tree_file_is_a_readable_error(world: GameWorld, tmp_path) -> None:
    from pacman_bteng.behaviors import BrainError

    with pytest.raises(BrainError, match="not found"):
        AgentBrain(world.ghost("blinky"), world, tmp_path / "nope.xml")


# ── The integration run ───────────────────────────────────────────────────────

def test_five_brains_play_a_real_game_headlessly(maze: Maze, settings: Settings) -> None:
    """Forty seconds of game time, five trees, one seed, no window.

    This is the test that would catch a tree that validates and then does
    nothing: it asserts the game *moves* — the score climbs, pellets vanish,
    and every ghost gets itself out of the pen.  IDLE is called out separately
    because it is the specific failure mode of the looping-tree pattern: a
    brain that returns IDLE has stopped ticking its root and would sit there
    forever without ever raising.
    """
    settings.autopilot = True                        # Pac-Man drives himself too
    world = GameWorld(
        maze, settings.rules, ghost_names=settings.ghosts, level=1, seed=SEED
    )
    pool = BrainPool(world, settings, introspection=None)

    try:
        assert [brain.name for brain in pool.brains] == [*GHOST_NAMES, "pacman"]

        start_playing(world)
        released = set()
        statuses = set()

        def observe() -> None:
            for brain in pool.brains:
                statuses.add(brain.status)
            for ghost in world.ghosts:
                if ghost.state not in (GhostState.IN_HOUSE, GhostState.LEAVING):
                    released.add(ghost.name)

        pellets_at_start = world.pellets_left
        # 30 fps rather than 60: the autopilot's path-metric guards each cost a
        # full-board BFS per tick, and this test is meant to prove the game
        # works, not to benchmark it.  The app caps a frame at 0.05 s anyway, so
        # a 33 ms step is well inside what the world is built to integrate.
        run_headless(world, pool, 40.0, dt=1.0 / 30.0, on_step=observe)

        assert world.score > 0
        assert world.pellets_left < pellets_at_start
        assert released == set(GHOST_NAMES), f"never left the pen: {set(GHOST_NAMES) - released}"
        assert NodeStatus.IDLE not in statuses
        assert world.phase is not GamePhase.GAME_OVER
    finally:
        pool.shutdown()


def test_a_brain_describes_the_branch_it_is_running(world: GameWorld, brains: BrainPool) -> None:
    start_playing(world)
    run_headless(world, brains, 3.0)

    for brain in brains.brains:
        assert brain.describe() != "idle"
        assert brain.cycles >= 0
        # The panel reads these; blank ones would leave a hole in the HUD.
        assert brain.branch or brain.active_node


def test_the_ghosts_stay_on_walkable_tiles_for_a_whole_run(
    world: GameWorld, brains: BrainPool
) -> None:
    """No agent may ever stand in a wall.

    Cheap, and it is the assertion that would catch a wrap bug, an overshoot
    bug or a door-permission bug — all three show up the same way.
    """
    start_playing(world)
    maze = world.maze

    def check() -> None:
        for agent in world.agents:
            assert maze.is_walkable(agent.tile, through_door=True), (
                f"{agent.name} is inside a wall at {agent.tile}"
            )

    run_headless(world, brains, 15.0, on_step=check)


def test_resetting_the_pool_sends_every_tree_back_to_idle(
    world: GameWorld, brains: BrainPool
) -> None:
    start_playing(world)
    run_headless(world, brains, 2.0)

    brains.reset_all()

    for brain in brains.brains:
        assert brain.status is NodeStatus.IDLE
        assert brain.branch == "" and brain.active_node == ""


def test_autopilot_can_be_attached_and_detached_at_runtime(
    world: GameWorld, settings: Settings
) -> None:
    # F2 in-game toggles this; building the tree on demand is why a player who
    # never presses it does not pay for the parse.
    pool = BrainPool(world, settings, introspection=None)
    try:
        assert pool.by_name("pacman") is None
        assert pool.set_autopilot(True, settings) is True
        assert pool.by_name("pacman") is not None
        assert pool.set_autopilot(True, settings) is False      # already on
        assert pool.set_autopilot(False, settings) is True
        assert pool.by_name("pacman") is None
    finally:
        pool.shutdown()
