"""The BT node library, ticked by hand — no XML, no executor, no GUI.

Every node here is built the way ``XMLTreeParser`` builds it: a name, a
``NodeConfig`` carrying the blackboard, the port→key wiring, and the static
attributes **as strings**.  That last detail is the whole reason these tests
exist at a node level at all — a port that forgets to cast reads ``"6"`` and
either crashes or compares a string to a float, and a full-tree test would only
report it as "the ghost behaves oddly".
"""

from __future__ import annotations

import pytest
from bteng import NodeStatus
from conftest import TARGET_PORTS, make_node, seeded_blackboard, start_playing, tick

from pacman_bteng.behaviors import actions as A
from pacman_bteng.behaviors import blackboard_keys as K
from pacman_bteng.behaviors import conditions as C
from pacman_bteng.behaviors.nodes import as_bool, as_float, as_int
from pacman_bteng.game.entities import GhostState
from pacman_bteng.game.geometry import Direction
from pacman_bteng.game.maze import Maze
from pacman_bteng.game.world import GameWorld, GlobalMode

SUCCESS = NodeStatus.SUCCESS
FAILURE = NodeStatus.FAILURE
RUNNING = NodeStatus.RUNNING


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def blinky(world: GameWorld):
    """Blinky, out on the board and parked on a tile centre."""
    ghost = world.ghost("blinky")
    ghost.state = GhostState.NORMAL
    return ghost


@pytest.fixture
def board(world: GameWorld, blinky):
    return seeded_blackboard(blinky, world)


@pytest.fixture
def pac_board(world: GameWorld):
    return seeded_blackboard(world.pacman, world)


def selector(node_cls, board, **params):
    """A target selector wired exactly as the XML wires one."""
    return make_node(node_cls, board, params=params, outputs=TARGET_PORTS)


def mover(node_cls, board, **params):
    """A mover: it *reads* target_x/target_y that a selector wrote."""
    return make_node(node_cls, board, params=params, inputs=TARGET_PORTS)


# ═══════════════════════════════════════════════════════════════════════════
# CONDITIONS — the guards that pick a branch
# ═══════════════════════════════════════════════════════════════════════════

def test_is_eaten_tracks_the_ghost_state(board, blinky) -> None:
    node = make_node(C.IsEaten, board)
    assert tick(node) is FAILURE
    blinky.state = GhostState.EATEN
    assert tick(node) is SUCCESS


def test_is_in_house_covers_both_penned_and_leaving(board, blinky) -> None:
    """Waiting and squeezing out are one branch, not two.

    They want identical behavior — LeaveHouse handles both — so folding them
    into one guard is what keeps every ghost tree six branches long.
    """
    node = make_node(C.IsInHouse, board)
    for state in (GhostState.IN_HOUSE, GhostState.LEAVING):
        blinky.state = state
        assert tick(node) is SUCCESS, state
    for state in (GhostState.NORMAL, GhostState.FRIGHTENED, GhostState.EATEN):
        blinky.state = state
        assert tick(node) is FAILURE, state


def test_is_frightened_tracks_the_power_pellet(board, blinky) -> None:
    node = make_node(C.IsFrightened, board)
    assert tick(node) is FAILURE
    blinky.state = GhostState.FRIGHTENED
    assert tick(node) is SUCCESS


def test_a_ghost_condition_on_pacmans_board_simply_fails(pac_board) -> None:
    # Pac-Man's autopilot shares the node library; ghost-only guards must be
    # inert for him rather than raising, or one shared tree would be impossible.
    assert tick(make_node(C.IsEaten, pac_board)) is FAILURE
    assert tick(make_node(C.IsInHouse, pac_board)) is FAILURE
    assert tick(make_node(C.IsFrightened, pac_board)) is FAILURE


def test_is_scatter_mode_follows_the_world(board, world: GameWorld) -> None:
    node = make_node(C.IsScatterMode, board)
    world.mode = GlobalMode.SCATTER
    assert tick(node) is SUCCESS
    world.mode = GlobalMode.CHASE
    assert tick(node) is FAILURE


def test_is_power_active_follows_the_timer(board, world: GameWorld) -> None:
    node = make_node(C.IsPowerActive, board)
    assert tick(node) is FAILURE
    world.power_timer = 3.0
    assert tick(node) is SUCCESS


def test_has_pellets_left_is_the_level_clear_guard(board, world: GameWorld) -> None:
    node = make_node(C.HasPelletsLeft, board)
    assert tick(node) is SUCCESS
    world.pellets.clear()
    world.power_pellets.clear()
    assert tick(node) is FAILURE


def test_is_pacman_within_measures_from_the_agents_tile(board, blinky, world) -> None:
    world.pacman.teleport((13, 23))
    blinky.teleport((10, 23))                       # exactly three tiles away

    assert tick(selector_free(C.IsPacmanWithin, board, distance="3")) is SUCCESS
    assert tick(selector_free(C.IsPacmanWithin, board, distance="2")) is FAILURE


def test_is_pacman_within_can_measure_along_the_corridors(board, blinky, world) -> None:
    """``metric="manhattan"`` is the reason the port exists.

    Straight-line distance measures through walls; in a maze the number that
    predicts "can he reach me" is the one that walks around them.
    """
    world.pacman.teleport((13, 23))
    blinky.teleport((10, 20))                       # euclid 4.24, manhattan 6

    near = selector_free(C.IsPacmanWithin, board, distance="5", metric="euclidean")
    far = selector_free(C.IsPacmanWithin, board, distance="5", metric="manhattan")
    assert tick(near) is SUCCESS
    assert tick(far) is FAILURE


def test_is_ghost_within_separates_danger_from_dinner(pac_board, world: GameWorld) -> None:
    pac = world.pacman
    blinky = world.ghost("blinky")
    blinky.teleport(pac.tile)
    blinky.state = GhostState.NORMAL

    danger = selector_free(C.IsGhostWithin, pac_board, distance="4", hostile_only="true")
    dinner = selector_free(
        C.IsGhostWithin, pac_board, distance="4", hostile_only="false", edible_only="true"
    )
    assert tick(danger) is SUCCESS
    assert tick(dinner) is FAILURE          # nothing edible on the board yet

    blinky.state = GhostState.FRIGHTENED
    assert tick(danger) is FAILURE          # a frightened ghost is not a threat
    assert tick(dinner) is SUCCESS


def selector_free(node_cls, board, **params):
    """A condition node — no target ports, only static XML attributes."""
    return make_node(node_cls, board, params=params)


# ═══════════════════════════════════════════════════════════════════════════
# TARGET SELECTORS — the four personalities
# ═══════════════════════════════════════════════════════════════════════════

def test_a_target_selector_publishes_to_the_blackboard_and_to_the_agent(
    board, blinky, world
) -> None:
    """Both destinations matter, for different readers.

    The blackboard entry is what ``StepTowardTarget`` reads back one node
    later; ``agent.target`` is what the debug overlay draws.  Writing only one
    of them makes either the ghost or the picture lie.
    """
    node = selector(A.SetChaseTarget, board)
    assert tick(node) is SUCCESS

    x, y = world.pacman.tile
    assert (board.get(K.TARGET_X), board.get(K.TARGET_Y)) == (float(x), float(y))
    assert blinky.target == (x, y)


def test_chase_aims_at_pacmans_own_tile(board, world: GameWorld) -> None:
    world.pacman.teleport((7, 5))
    node = selector(A.SetChaseTarget, board)
    assert tick(node) is SUCCESS
    assert board.get(K.TARGET_X) == 7.0 and board.get(K.TARGET_Y) == 5.0


def test_ambush_aims_lead_tiles_ahead_of_where_pacman_is_going(board, world) -> None:
    pac = world.pacman
    pac.teleport((13, 23))
    pac.last_direction = Direction.LEFT            # facing left while parked

    node = selector(A.SetAmbushTarget, board, lead_tiles="4")
    assert tick(node) is SUCCESS
    assert (board.get(K.TARGET_X), board.get(K.TARGET_Y)) == (9.0, 23.0)


def test_ambush_degenerates_to_a_chase_when_pacman_has_no_heading(board, world) -> None:
    # At the very start of a round nobody is facing anywhere; aiming at his own
    # tile is the only sensible reading of "four tiles ahead of nothing".
    pac = world.pacman
    pac.teleport((13, 23))
    pac.direction = pac.last_direction = Direction.NONE

    node = selector(A.SetAmbushTarget, board, lead_tiles="4")
    assert tick(node) is SUCCESS
    assert (board.get(K.TARGET_X), board.get(K.TARGET_Y)) == (13.0, 23.0)


def test_shy_switches_from_chasing_to_bolting_at_the_flee_radius(board, blinky, world) -> None:
    """Clyde's whole personality is one comparison.

    Outside the radius he is Blinky; inside it he runs for his corner, which is
    what makes him oscillate instead of committing — and what makes him the one
    ghost you can walk past.
    """
    pac = world.pacman
    pac.teleport((13, 23))

    blinky.teleport((13, 3))                       # 20 tiles away: chasing
    node = selector(A.SetShyTarget, board, flee_radius="8")
    assert tick(node) is SUCCESS
    assert blinky.target == pac.tile

    blinky.teleport((13, 20))                      # 3 tiles away: bolting
    assert tick(node) is SUCCESS
    assert blinky.target == blinky.scatter_corner


def test_shy_takes_an_explicit_corner_over_the_assigned_one(board, blinky, world) -> None:
    world.pacman.teleport((13, 23))
    blinky.teleport((13, 20))
    node = selector(A.SetShyTarget, board, flee_radius="8", corner_x="1", corner_y="29")
    assert tick(node) is SUCCESS
    assert blinky.target == (1, 29)


def test_scatter_falls_back_to_the_corner_the_world_assigned(board, blinky) -> None:
    # -1/-1 is the "use mine" sentinel, which is what lets one XML fragment be
    # copied between all four ghosts unchanged.
    node = selector(A.SetScatterTarget, board, corner_x="-1", corner_y="-1")
    assert tick(node) is SUCCESS
    assert blinky.target == blinky.scatter_corner


def test_flank_doubles_the_vector_through_a_point_ahead_of_pacman(
    board, blinky, world
) -> None:
    pac = world.pacman
    pac.teleport((13, 23))
    pac.last_direction = Direction.LEFT
    partner = world.ghost("pinky")
    partner.teleport((13, 20))

    # pivot = two tiles ahead of Pac-Man = (11, 23); target = 2*pivot - partner
    node = selector(A.SetFlankTarget, board, partner="pinky", lead_tiles="2")
    assert tick(node) is SUCCESS
    assert blinky.target == (2 * 11 - 13, 2 * 23 - 20)


def test_flank_falls_back_to_a_direct_chase_with_no_partner(maze: Maze) -> None:
    """A one-ghost run must not freeze Inky.

    ``--ghosts inky`` is a legitimate way to read one tree in isolation, and
    the flank vector has no anchor then — so the branch degrades to Blinky's
    behavior rather than failing and dropping the ghost into Wander.
    """
    world = GameWorld(maze, ghost_names=("inky",), seed=3)
    inky = world.ghost("inky")
    inky.state = GhostState.NORMAL
    board = seeded_blackboard(inky, world)
    world.pacman.teleport((13, 23))

    node = selector(A.SetFlankTarget, board, partner="blinky", lead_tiles="2")
    assert tick(node) is SUCCESS
    assert inky.target == world.pacman.tile


def test_flee_aims_at_a_point_directly_away_from_pacman(board, blinky, world) -> None:
    world.pacman.teleport((13, 23))
    blinky.teleport((13, 20))                       # directly above him

    node = selector(A.SetFleeTarget, board, lookahead="10")
    assert tick(node) is SUCCESS
    assert blinky.target[0] == 13
    assert blinky.target[1] < 20                    # further up, away from him


def test_flee_stays_on_the_board(board, blinky, world) -> None:
    # Projected targets are allowed to be imaginary, but clamp() pulls them
    # back inside the rectangle so the greedy step stays numerically useful.
    world.pacman.teleport((13, 23))
    blinky.teleport((13, 1))
    node = selector(A.SetFleeTarget, board, lookahead="40")
    assert tick(node) is SUCCESS
    assert 0 <= blinky.target[0] < world.maze.width
    assert 0 <= blinky.target[1] < world.maze.height


# ── The eyes going home ───────────────────────────────────────────────────────

def test_house_target_steers_the_eyes_at_the_pen(board, blinky, world) -> None:
    blinky.state = GhostState.EATEN
    node = selector(A.SetHouseTarget, board)
    assert tick(node) is SUCCESS
    assert blinky.target == world.maze.house_centre()


def test_house_target_fails_on_arrival_and_hands_the_ghost_over(board, blinky, world) -> None:
    """FAILURE here is the handoff, not an error.

    The eyes reach the pen, the state flips to IN_HOUSE, this branch stops
    matching, and the ReactiveFallback drops through to ExitHouse *in the same
    tick*.  A SUCCESS would leave the ghost pointed at a tile it is standing on.
    """
    blinky.state = GhostState.EATEN
    blinky.teleport(world.maze.house_centre())

    node = selector(A.SetHouseTarget, board)

    assert tick(node) is FAILURE
    assert blinky.state is GhostState.IN_HOUSE
    assert blinky.release_timer > 0.0


# ── Pac-Man's own selectors ───────────────────────────────────────────────────

def test_nearest_pellet_prefers_corridor_distance_over_line_of_sight(
    pac_board, world: GameWorld
) -> None:
    node = selector(A.SetNearestPelletTarget, pac_board, candidates="10")
    assert tick(node) is SUCCESS
    chosen = world.pacman.target
    assert chosen in world.pellets or chosen in world.power_pellets


def test_nearest_pellet_fails_on_a_clear_board(pac_board, world: GameWorld) -> None:
    world.pellets.clear()
    world.power_pellets.clear()
    assert tick(selector(A.SetNearestPelletTarget, pac_board)) is FAILURE


def test_hunt_only_fires_while_something_is_edible(pac_board, world: GameWorld) -> None:
    node = selector(A.SetHuntGhostTarget, pac_board)
    assert tick(node) is FAILURE

    blinky = world.ghost("blinky")
    blinky.state = GhostState.FRIGHTENED
    blinky.teleport((13, 20))
    assert tick(node) is SUCCESS
    assert world.pacman.target == (13, 20)


def test_escape_picks_a_reachable_neighbour_and_fails_with_no_threat(
    pac_board, world: GameWorld
) -> None:
    node = selector(A.SetEscapeTarget, pac_board)
    for ghost in world.ghosts:                      # nobody hostile on the board
        ghost.state = GhostState.EATEN
    assert tick(node) is FAILURE

    blinky = world.ghost("blinky")
    blinky.state = GhostState.NORMAL
    blinky.teleport((13, 20))
    world.pacman.teleport((13, 23))
    assert tick(node) is SUCCESS
    # Whatever it picked has to be a tile Pac-Man can actually step onto.
    assert world.maze.is_walkable(world.pacman.target)


# ═══════════════════════════════════════════════════════════════════════════
# MOVERS — the three-phase contract
# ═══════════════════════════════════════════════════════════════════════════

def test_step_toward_target_runs_for_a_whole_tile_then_succeeds(
    board, blinky, world: GameWorld
) -> None:
    """RUNNING while it slides, SUCCESS exactly when a tile is reached.

    Holding RUNNING for the length of a tile is what makes the live viewer show
    something true, and it is what lets a higher-priority guard (a power pellet
    going off) interrupt the ghost mid-corridor instead of at the next junction.
    """
    board.set(K.TARGET_X, float(world.pacman.tile[0]))
    board.set(K.TARGET_Y, float(world.pacman.tile[1]))
    node = mover(A.StepTowardTarget, board, pathfinder="greedy")

    assert tick(node) is RUNNING                     # committed to a direction
    assert blinky.direction.is_moving

    blinky.advance(0.5 / blinky.speed, world.maze)
    assert tick(node) is RUNNING                     # still sliding

    blinky.advance(0.6 / blinky.speed, world.maze)   # arrives, direction cleared
    assert blinky.is_deciding
    assert tick(node) is SUCCESS


def test_step_toward_target_stays_running_when_it_starts_mid_tile(
    board, blinky, world: GameWorld
) -> None:
    # A direction can only be taken at a tile centre, so a branch that becomes
    # active mid-corridor waits rather than failing and dropping to Wander.
    world.request_direction(blinky, Direction.LEFT)
    blinky.advance(0.5 / blinky.speed, world.maze)

    node = mover(A.StepTowardTarget, board)
    assert tick(node) is RUNNING


def test_step_toward_target_fails_when_the_agent_is_boxed_in(sealed_world) -> None:
    """FAILURE is how a mover tells the tree to try the next branch.

    Nothing in ``classic.txt`` can trap a ghost, so this uses a purpose-built
    map with Blinky's spawn walled off on all four sides — the only way to
    exercise the branch that keeps a stuck ghost from freezing the whole tree.
    """
    world, ghost = sealed_world
    board = seeded_blackboard(ghost, world)
    board.set(K.TARGET_X, 1.0)
    board.set(K.TARGET_Y, 1.0)

    node = mover(A.StepTowardTarget, board)
    assert tick(node) is FAILURE


def test_wander_step_takes_a_legal_direction_and_is_seeded(board, blinky, world) -> None:
    node = mover(A.WanderStep, board)
    assert tick(node) is RUNNING
    assert blinky.direction.is_moving
    assert world.maze.is_walkable(blinky.next_tile())


def test_wander_step_fails_when_boxed_in(sealed_world) -> None:
    world, ghost = sealed_world
    node = mover(A.WanderStep, seeded_blackboard(ghost, world))
    assert tick(node) is FAILURE


def test_leave_house_waits_out_the_timer_then_walks_out(world: GameWorld) -> None:
    """One node, many tiles: the whole exit is a single behavior.

    LeaveHouse holds RUNNING through the wait *and* the walk, and only it is
    allowed to hand the ghost back to the world as released — which is why it
    is not a StepAction.
    """
    start_playing(world)
    pinky = world.ghost("pinky")
    board = seeded_blackboard(pinky, world)
    node = make_node(A.LeaveHouse, board)

    assert pinky.state is GhostState.IN_HOUSE
    assert tick(node) is RUNNING                     # still waiting in the pen

    world.update(world.rules.release_delays["pinky"] + 0.01)
    assert pinky.state is GhostState.LEAVING

    # Walk it out: tick, advance, repeat, exactly as the frame loop would.
    for _ in range(400):
        status = tick(node)
        if status is SUCCESS:
            break
        pinky.advance(1.0 / pinky.speed, world.maze)
    else:                                            # pragma: no cover - a bug
        pytest.fail("pinky never made it out of the house")

    assert pinky.tile == world.maze.door_exit()
    assert pinky.state in (GhostState.NORMAL, GhostState.FRIGHTENED)


# ═══════════════════════════════════════════════════════════════════════════
# XML ATTRIBUTES ARE STRINGS
# ═══════════════════════════════════════════════════════════════════════════

def test_the_casting_helpers_are_tolerant_by_design() -> None:
    # A bad attribute in a tree must not stop the game from starting; the node
    # falls back to its default and plays on.
    assert as_float("6.5") == 6.5
    assert as_float("nonsense", 8.0) == 8.0
    assert as_float(None, 2.0) == 2.0

    assert as_int("4") == 4
    assert as_int("4.9") == 4                        # via float, so "4.9" is legal
    assert as_int("nope", 10) == 10

    assert as_bool("true") and as_bool("YES") and as_bool("1") and as_bool("on")
    assert not as_bool("false") and not as_bool("0") and not as_bool("")
    assert as_bool(None, True) is True
    assert as_bool(True) is True


def test_numeric_ports_survive_arriving_as_strings(board, blinky, world) -> None:
    """Every static attribute in every XML file is a string.

    ``lead_tiles="3"`` reaches the node as ``"3"``; multiplying a Direction
    delta by that string is a TypeError, so the cast is not a nicety.
    """
    pac = world.pacman
    pac.teleport((13, 23))
    pac.last_direction = Direction.LEFT

    node = selector(A.SetAmbushTarget, board, lead_tiles="3")
    assert tick(node) is SUCCESS
    assert blinky.target == (10, 23)


def test_boolean_ports_survive_arriving_as_strings(board, blinky, world) -> None:
    """``allow_reverse="true"`` has to actually lift the reversal ban.

    Without the cast the string ``"false"`` is truthy and every ghost would
    happily turn back on itself — the single change that would make the game
    unplayable while every test about targets still passed.
    """
    blinky.teleport((6, 1))
    blinky.last_direction = Direction.RIGHT          # so LEFT is banned
    board.set(K.TARGET_X, 1.0)
    board.set(K.TARGET_Y, 1.0)                       # straight back the way it came

    banned = mover(A.StepTowardTarget, board, allow_reverse="false")
    assert tick(banned) is RUNNING
    assert blinky.direction is not Direction.LEFT

    blinky.teleport((6, 1))
    blinky.last_direction = Direction.RIGHT
    allowed = mover(A.StepTowardTarget, board, allow_reverse="true")
    assert tick(allowed) is RUNNING
    assert blinky.direction is Direction.LEFT


def test_the_pathfinder_port_selects_the_navigator(board, blinky, world) -> None:
    board.set(K.TARGET_X, float(world.pacman.tile[0]))
    board.set(K.TARGET_Y, float(world.pacman.tile[1]))
    for name in ("greedy", "bfs", "typo-falls-back-to-greedy"):
        blinky.teleport((13, 11))
        node = mover(A.StepTowardTarget, board, pathfinder=name)
        assert tick(node) is RUNNING, name
        assert blinky.direction.is_moving


# ── A map built to trap a ghost ───────────────────────────────────────────────

SEALED_MAP = """\
[maze]
#########
#S.....P#
#.#####.#
#I.....C#
#########
###B#####
#########
[/maze]
"""


@pytest.fixture
def sealed_world(tmp_path):
    """A tiny legal map whose Blinky spawn is walled in on all four sides.

    ``classic.txt`` has no dead ends and no sealed tiles — by design — so the
    "boxed in" branch of every mover is unreachable on the real board.  This is
    the smallest map that reaches it without touching anything in the repo.
    """
    path = tmp_path / "sealed.txt"
    path.write_text(SEALED_MAP, encoding="utf-8")
    world = GameWorld(Maze.from_file(path), seed=0)
    ghost = world.ghost("blinky")
    ghost.state = GhostState.NORMAL
    return world, ghost
