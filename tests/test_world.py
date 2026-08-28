"""The rules of Pac-Man, with no renderer and no behavior trees attached.

Most of these tests drive the world with ``update(0.0)``: a zero-length frame
runs every rule — eating, colliding, the level-clear check — while moving
nobody, which is what makes the assertions about *one* rule instead of about
the two tiles of travel that happened alongside it.
"""

from __future__ import annotations

import pytest
from conftest import start_playing

from pacman_bteng.game.entities import GhostState
from pacman_bteng.game.geometry import Direction
from pacman_bteng.game.maze import Maze
from pacman_bteng.game.world import GamePhase, GameWorld, GlobalMode


def kinds(events) -> list:
    return [event.kind for event in events]


# ── Eating ────────────────────────────────────────────────────────────────────

def test_a_pellet_scores_ten_and_leaves_the_board(world: GameWorld) -> None:
    start_playing(world)
    tile = sorted(world.pellets)[0]
    world.pacman.teleport(tile)
    before = world.pellets_left

    events = world.update(0.0)

    assert world.score == world.rules.points_pellet == 10
    assert tile not in world.pellets
    assert world.pellets_left == before - 1
    assert kinds(events) == ["pellet"]


def test_a_power_pellet_scores_fifty_starts_the_timer_and_frightens_the_board(
    world: GameWorld,
) -> None:
    start_playing(world)
    tile = sorted(world.power_pellets)[0]
    world.pacman.teleport(tile)

    events = world.update(0.0)

    assert world.score == 50
    assert world.power_active and world.power_timer == world.rules.power_duration
    assert kinds(events) == ["power"]
    # Only the ghosts that are actually out on the board turn; the three still
    # penned keep their release timers, which is what stops a power pellet from
    # emptying the house early.
    assert world.ghost("blinky").state is GhostState.FRIGHTENED
    for name in ("pinky", "inky", "clyde"):
        assert world.ghost(name).state is GhostState.IN_HOUSE


def test_a_frightened_ghost_reverses_when_the_power_pellet_lands(world: GameWorld) -> None:
    start_playing(world)
    blinky = world.ghost("blinky")
    blinky.last_direction = Direction.LEFT
    world.pacman.teleport(sorted(world.power_pellets)[0])

    world.update(0.0)

    assert blinky.last_direction is Direction.RIGHT


def test_the_power_timer_expires_and_hands_the_ghosts_back(world: GameWorld) -> None:
    start_playing(world)
    world.pacman.teleport(sorted(world.power_pellets)[0])
    world.update(0.0)

    world.update(world.rules.power_duration)

    assert not world.power_active
    assert world.ghost("blinky").state is GhostState.NORMAL
    assert world.ghost_chain_index == 0


# ── The ghost chain ───────────────────────────────────────────────────────────

def test_the_ghost_chain_doubles_within_one_power_pellet_and_resets_after(
    world: GameWorld,
) -> None:
    """200 / 400 / 800 / 1600, then back to 200 on the next pellet.

    The chain index lives on the world rather than on Pac-Man because it is a
    property of the *fright*, not of the player: it has to survive being eaten
    in four separate collisions and die the moment the timer runs out.
    """
    start_playing(world)
    for ghost in world.ghosts:
        ghost.state = GhostState.FRIGHTENED
        ghost.teleport(world.pacman.tile)
    world.power_timer = 5.0

    events = [e for e in world.update(0.0) if e.kind == "ghost_eaten"]

    assert [e.points for e in events] == [200, 400, 800, 1600]
    assert world.score == 3000
    assert all(g.state is GhostState.EATEN for g in world.ghosts)

    # The fright ends -> the chain resets, so the next pellet starts at 200.
    world.power_timer = 0.001
    world.update(0.01)
    assert world.ghost_chain_index == 0


def test_an_eaten_ghost_is_neither_edible_nor_hostile(world: GameWorld) -> None:
    start_playing(world)
    blinky = world.ghost("blinky")
    blinky.state = GhostState.EATEN
    blinky.teleport(world.pacman.tile)
    before = world.lives

    world.update(0.0)

    # Eyes pass straight through Pac-Man; that is the whole point of the state.
    assert world.lives == before
    assert world.phase is GamePhase.PLAYING


# ── Dying ─────────────────────────────────────────────────────────────────────

def test_a_hostile_collision_costs_a_life_and_enters_dying(world: GameWorld) -> None:
    start_playing(world)
    blinky = world.ghost("blinky")
    blinky.state = GhostState.NORMAL
    blinky.teleport(world.pacman.tile)
    before = world.lives

    events = world.update(0.0)

    assert world.lives == before - 1
    assert world.phase is GamePhase.DYING
    assert not world.pacman.alive
    assert "pacman_caught" in kinds(events)


def test_the_last_life_ends_the_game(world: GameWorld) -> None:
    start_playing(world)
    world.lives = 1
    blinky = world.ghost("blinky")
    blinky.state = GhostState.NORMAL
    blinky.teleport(world.pacman.tile)

    world.update(0.0)
    assert world.phase is GamePhase.DYING
    assert not world.is_over          # the death animation still has to play

    events = world.update(world.rules.dying_duration)

    assert world.phase is GamePhase.GAME_OVER
    assert world.is_over
    assert "game_over" in kinds(events)


def test_a_death_with_lives_left_restarts_the_round_not_the_level(world: GameWorld) -> None:
    start_playing(world)
    eaten = sorted(world.pellets)[0]
    world.pellets.discard(eaten)
    world.ghost("blinky").teleport(world.pacman.tile)

    world.update(0.0)
    world.update(world.rules.dying_duration)

    assert world.phase is GamePhase.READY
    assert world.pacman.tile == world.maze.spawns["pacman"]
    # The board is NOT restocked — losing a life must not undo the eating.
    assert eaten not in world.pellets


# ── Clearing the board ────────────────────────────────────────────────────────

def test_an_empty_board_clears_the_level_and_then_starts_the_next(world: GameWorld) -> None:
    start_playing(world)
    world.pellets.clear()
    world.power_pellets.clear()

    events = world.update(0.0)
    assert world.phase is GamePhase.LEVEL_CLEAR
    assert "level_clear" in kinds(events)

    events = world.update(world.rules.level_clear_duration)

    assert world.level == 2
    assert world.phase is GamePhase.READY
    assert world.pellets_left == world.maze.total_pellets()   # board restocked
    assert "level_start" in kinds(events)


def test_the_next_level_speeds_everyone_up(world: GameWorld) -> None:
    start_playing(world)
    base = world.pacman.speed
    world.pellets.clear()
    world.power_pellets.clear()
    world.update(0.0)
    world.update(world.rules.level_clear_duration)

    assert world.pacman.speed == pytest.approx(base + world.rules.level_speed_bonus)


# ── The controller handshake ──────────────────────────────────────────────────

def test_request_direction_refuses_a_blocked_direction(world: GameWorld) -> None:
    pac = world.pacman                       # spawns in a horizontal corridor
    assert world.request_direction(pac, Direction.UP) is False
    assert pac.direction is Direction.NONE
    assert world.request_direction(pac, Direction.RIGHT) is True
    assert pac.direction is Direction.RIGHT


def test_request_direction_refuses_a_choice_made_mid_tile(world: GameWorld) -> None:
    """A direction may only be taken at a tile centre — the decision point.

    Allowing it mid-tile would let an agent pivot 90 degrees inside a wall,
    which is exactly the bug the ``is_deciding`` handshake exists to prevent.
    """
    pac = world.pacman
    assert world.request_direction(pac, Direction.RIGHT) is True
    pac.advance(0.5 / pac.speed, world.maze)          # halfway to the next tile

    assert pac.is_deciding is False
    assert world.request_direction(pac, Direction.LEFT) is False
    assert pac.direction is Direction.RIGHT


def test_request_direction_refuses_none(world: GameWorld) -> None:
    assert world.request_direction(world.pacman, Direction.NONE) is False


def test_a_ghost_may_cross_the_door_only_while_leaving_or_eaten(world: GameWorld) -> None:
    maze = world.maze
    pinky = world.ghost("pinky")
    door_above = maze.door_exit()
    pinky.teleport(Direction.DOWN.apply(Direction.DOWN.apply(door_above)))  # under the door

    pinky.state = GhostState.NORMAL
    assert world.request_direction(pinky, Direction.UP) is False
    pinky.state = GhostState.LEAVING
    assert world.request_direction(pinky, Direction.UP) is True


# ── Movement and the decision-point contract ──────────────────────────────────

def test_advance_clears_the_direction_on_arrival_and_carries_the_overshoot(
    world: GameWorld,
) -> None:
    """Arriving on a tile is what parks an agent for a new decision.

    The leftover progress is kept so a turn does not cost the agent a fraction
    of a tile of speed — but ``direction`` must be NONE regardless, or the next
    controller tick would be steering an agent that never asked for advice.
    """
    pac = world.pacman
    start = pac.tile
    world.request_direction(pac, Direction.RIGHT)

    assert pac.advance(0.5 / pac.speed, world.maze) is False
    assert pac.direction is Direction.RIGHT
    assert pac.progress == pytest.approx(0.5)

    assert pac.advance(0.9 / pac.speed, world.maze) is True
    assert pac.tile == Direction.RIGHT.apply(start)
    assert pac.direction is Direction.NONE            # the decision point
    assert pac.last_direction is Direction.RIGHT
    assert pac.progress == pytest.approx(0.4)         # 1.4 travelled, 1.0 spent
    assert pac.is_deciding


def test_a_huge_frame_never_carries_more_than_one_tile_of_overshoot(
    world: GameWorld,
) -> None:
    # A dragged window or a breakpoint produces a multi-second dt; capping the
    # carry-over is what stops that from teleporting an agent through walls.
    pac = world.pacman
    world.request_direction(pac, Direction.RIGHT)
    pac.advance(10.0, world.maze)
    assert pac.progress <= 0.99


def test_position_is_the_exact_tile_centre_while_parked(world: GameWorld) -> None:
    pac = world.pacman
    pac.progress = 0.7                       # left over from the previous leg
    assert pac.position == (float(pac.tile[0]), float(pac.tile[1]))


# ── The scatter / chase schedule ──────────────────────────────────────────────

def test_the_schedule_advances_and_turns_every_ghost_around(world: GameWorld) -> None:
    """A mode switch reverses the ghosts on the board — the arcade's own tell.

    It is the one moment the no-reversing rule is broken, and the world breaks
    it by rewriting ``last_direction`` rather than by touching ``direction``,
    so a ghost mid-tile finishes its tile first.
    """
    start_playing(world)
    scatter_seconds = float(world.rules.mode_schedule[0][1])
    blinky = world.ghost("blinky")
    blinky.last_direction = Direction.LEFT
    assert world.mode is GlobalMode.SCATTER

    events = world.update(scatter_seconds + 0.01)

    assert world.mode is GlobalMode.CHASE
    assert "mode" in kinds(events)
    assert blinky.last_direction is Direction.RIGHT


def test_the_schedule_is_frozen_while_a_power_pellet_burns(world: GameWorld) -> None:
    # The arcade suspends scatter/chase for the whole fright; without this the
    # timer would tick down underneath the player and the ghosts would reverse
    # the instant the fright ended.
    start_playing(world)
    world.power_timer = 30.0
    scatter_seconds = float(world.rules.mode_schedule[0][1])

    world.update(scatter_seconds + 0.01)

    assert world.mode is GlobalMode.SCATTER
    assert world.power_timer == pytest.approx(30.0 - scatter_seconds - 0.01)


def test_the_final_schedule_entry_lasts_the_rest_of_the_level(world: GameWorld) -> None:
    start_playing(world)
    for _ in range(len(world.rules.mode_schedule)):
        world.update(25.0)
    assert world.mode is GlobalMode(world.rules.mode_schedule[-1][0])
    world.update(600.0)
    assert world.mode is GlobalMode(world.rules.mode_schedule[-1][0])


# ── The tunnel ────────────────────────────────────────────────────────────────

def test_walking_off_the_left_edge_of_the_tunnel_row_arrives_on_the_right(
    world: GameWorld,
) -> None:
    maze = world.maze
    row = maze.tunnel_rows[0]
    pac = world.pacman
    pac.teleport((0, row))

    assert world.request_direction(pac, Direction.LEFT) is True
    pac.advance(1.0 / pac.speed, maze)

    assert pac.tile == (maze.width - 1, row)


def test_ghosts_crawl_through_the_tunnel_but_eyes_do_not(world: GameWorld) -> None:
    row = world.maze.tunnel_rows[0]
    blinky = world.ghost("blinky")
    blinky.teleport((0, row))

    blinky.state = GhostState.NORMAL
    assert world._speed_factor(blinky) == world.rules.tunnel_speed_factor
    blinky.state = GhostState.EATEN
    assert world._speed_factor(blinky) == 1.0


# ── Housekeeping helpers the behavior trees call ──────────────────────────────

def test_on_ghost_left_house_respects_an_active_power_pellet(world: GameWorld) -> None:
    pinky = world.ghost("pinky")
    world.on_ghost_left_house(pinky)
    assert pinky.state is GhostState.NORMAL

    world.power_timer = 3.0
    world.on_ghost_left_house(pinky)
    assert pinky.state is GhostState.FRIGHTENED


def test_on_ghost_reached_house_revives_with_a_short_release_timer(world: GameWorld) -> None:
    blinky = world.ghost("blinky")
    blinky.state = GhostState.EATEN
    world.on_ghost_reached_house(blinky)

    assert blinky.state is GhostState.IN_HOUSE
    assert blinky.release_timer > 0.0
    assert "ghost_home" in kinds(world.events)


def test_the_release_timers_count_down_and_open_the_pen(world: GameWorld) -> None:
    start_playing(world)
    pinky = world.ghost("pinky")
    assert pinky.state is GhostState.IN_HOUSE

    world.update(world.rules.release_delays["pinky"] + 0.01)

    assert pinky.state is GhostState.LEAVING


def test_a_paused_world_does_nothing_at_all(world: GameWorld) -> None:
    start_playing(world)
    world.paused = True
    world.pacman.teleport(sorted(world.pellets)[0])

    assert world.update(1.0) == []
    assert world.score == 0
    assert world.elapsed == 0.0


def test_an_extra_life_is_awarded_at_the_configured_score(maze: Maze) -> None:
    world = start_playing(GameWorld(maze, seed=1))
    world.score = world.rules.extra_life_at - 10
    world.pacman.teleport(sorted(world.pellets)[0])

    events = world.update(0.0)

    assert world.lives == world.rules.lives + 1
    assert "extra_life" in kinds(events)
