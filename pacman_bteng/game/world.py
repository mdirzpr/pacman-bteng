"""GameWorld — the rules of Pac-Man, with no renderer and no behavior trees.

The world owns every piece of mutable state (pellets, score, lives, timers) and
advances it in :meth:`GameWorld.update`.  It never decides where an agent goes:
controllers push a direction in through :meth:`GameWorld.request_direction`
whenever an agent is at a decision point.  That is the whole interface the
behavior-tree layer needs, which is what keeps this module import-free of BTEng.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from pacman_bteng.game.entities import Agent, Ghost, GhostState, Pacman
from pacman_bteng.game.geometry import Direction, Tile, euclidean
from pacman_bteng.game.maze import Maze

#: Distance in tiles at which Pac-Man and a ghost are considered to have met.
COLLISION_RADIUS = 0.7


class GlobalMode(Enum):
    """The scatter/chase rhythm every non-frightened ghost follows."""

    SCATTER = "scatter"
    CHASE = "chase"


class GamePhase(Enum):
    """Top-level state machine of a run."""

    READY = "ready"                # short countdown before the agents move
    PLAYING = "playing"
    DYING = "dying"                # Pac-Man was caught; freeze, then respawn
    LEVEL_CLEAR = "level_clear"    # board eaten; freeze, then next level
    GAME_OVER = "game_over"


@dataclass
class GameEvent:
    """Something worth reporting to the GUI (and to the console log)."""

    kind: str
    message: str = ""
    tile: Optional[Tile] = None
    points: int = 0


@dataclass
class Rules:
    """Tunables — every value comes from ``config/game.yaml``."""

    lives: int = 3
    pacman_speed: float = 8.0
    ghost_speed: float = 7.4
    frightened_speed: float = 4.8
    eaten_speed: float = 16.0
    tunnel_speed_factor: float = 0.55
    power_duration: float = 7.0
    power_blink_lead: float = 2.0
    level_speed_bonus: float = 0.35
    max_speed: float = 12.0
    ready_duration: float = 2.0
    dying_duration: float = 1.6
    level_clear_duration: float = 2.4
    release_delays: Dict[str, float] = field(
        default_factory=lambda: {"blinky": 0.0, "pinky": 2.0, "inky": 6.0, "clyde": 10.0}
    )
    mode_schedule: Sequence[Tuple[str, float]] = (
        ("scatter", 7.0), ("chase", 20.0),
        ("scatter", 7.0), ("chase", 20.0),
        ("scatter", 5.0), ("chase", 20.0),
        ("scatter", 5.0), ("chase", -1.0),   # -1 = for the rest of the level
    )
    scatter_corners: Dict[str, Tile] = field(
        default_factory=lambda: {
            "blinky": (26, 1),    # top right
            "pinky": (1, 1),      # top left
            "inky": (26, 29),     # bottom right
            "clyde": (1, 29),     # bottom left
        }
    )
    points_pellet: int = 10
    points_power: int = 50
    ghost_chain: Sequence[int] = (200, 400, 800, 1600)
    extra_life_at: int = 10_000


class GameWorld:
    """A single Pac-Man game in progress."""

    def __init__(
        self,
        maze: Maze,
        rules: Optional[Rules] = None,
        *,
        ghost_names: Sequence[str] = ("blinky", "pinky", "inky", "clyde"),
        level: int = 1,
        seed: Optional[int] = None,
    ) -> None:
        self.maze = maze
        self.rules = rules or Rules()
        self.rng = random.Random(seed)
        self.level = level

        self.score = 0
        self.lives = self.rules.lives
        self.high_score = 0
        self._next_extra_life = self.rules.extra_life_at

        self.pacman = Pacman(
            name="pacman",
            home=maze.spawns["pacman"],
            tile=maze.spawns["pacman"],
            speed=self.rules.pacman_speed,
        )
        self.ghosts: List[Ghost] = [
            Ghost(
                name=name,
                home=maze.spawns[name],
                tile=maze.spawns[name],
                speed=self.rules.ghost_speed,
                scatter_corner=self.rules.scatter_corners.get(name, (1, 1)),
            )
            for name in ghost_names
            if name in maze.spawns
        ]
        self._ghosts_by_name = {ghost.name: ghost for ghost in self.ghosts}

        self.pellets: set = set()
        self.power_pellets: set = set()
        self.phase = GamePhase.READY
        self.phase_timer = 0.0
        self.mode = GlobalMode.SCATTER
        self.power_timer = 0.0
        self.ghost_chain_index = 0
        self.elapsed = 0.0
        self.paused = False
        self.events: List[GameEvent] = []

        self._mode_index = 0
        self._mode_timer = 0.0
        self.start_level(level, full_reset=True)

    # ── Level / round setup ───────────────────────────────────────────────────

    def start_level(self, level: int, *, full_reset: bool = False) -> None:
        """Rebuild the board for *level* and go back to the READY phase."""
        self.level = level
        self.pellets = set(self.maze.pellets)
        self.power_pellets = set(self.maze.power_pellets)
        if full_reset:
            self.score = 0
            self.lives = self.rules.lives
            self._next_extra_life = self.rules.extra_life_at
        self.reset_round()

    def reset_round(self) -> None:
        """Send every agent home and restart the READY countdown."""
        self.pacman.reset()
        self.pacman.speed = self._level_speed(self.rules.pacman_speed)
        for ghost in self.ghosts:
            ghost.reset()
            ghost.speed = self._level_speed(self.rules.ghost_speed)
            ghost.release_timer = self.rules.release_delays.get(ghost.name, 0.0)
            # Blinky starts on the board; the others wait in the pen.
            if self.maze.is_house(ghost.tile):
                ghost.state = GhostState.IN_HOUSE
            else:
                ghost.state = GhostState.NORMAL
        self.power_timer = 0.0
        self.ghost_chain_index = 0
        self._mode_index = 0
        self._mode_timer = 0.0
        self.mode = GlobalMode(self.rules.mode_schedule[0][0])
        self.phase = GamePhase.READY
        self.phase_timer = self.rules.ready_duration

    def _level_speed(self, base: float) -> float:
        return min(base + self.rules.level_speed_bonus * (self.level - 1), self.rules.max_speed)

    # ── Queries used by the behavior trees ────────────────────────────────────

    def ghost(self, name: str) -> Optional[Ghost]:
        return self._ghosts_by_name.get(name)

    @property
    def agents(self) -> List[Agent]:
        return [self.pacman, *self.ghosts]

    @property
    def power_active(self) -> bool:
        return self.power_timer > 0.0

    @property
    def pellets_left(self) -> int:
        return len(self.pellets) + len(self.power_pellets)

    def pellet_tiles(self) -> List[Tile]:
        """Every remaining pellet, power pellets included."""
        return [*self.pellets, *self.power_pellets]

    def edible_ghosts(self) -> List[Ghost]:
        return [g for g in self.ghosts if g.is_edible]

    def hostile_ghosts(self) -> List[Ghost]:
        return [g for g in self.ghosts if g.is_hostile]

    def nearest_ghost(self, tile: Tile, *, hostile_only: bool = True) -> Optional[Ghost]:
        pool = self.hostile_ghosts() if hostile_only else list(self.ghosts)
        if not pool:
            return None
        return min(pool, key=lambda g: euclidean(tile, g.tile))

    def distance_to_pacman(self, tile: Tile) -> float:
        return euclidean(tile, self.pacman.tile)

    def passable_for(self, agent: Agent) -> Callable[[Tile], bool]:
        """Predicate deciding which tiles *agent* may walk on right now.

        The only difference between agents is the ghost-house door: Pac-Man can
        never cross it, and a ghost only while it is leaving or returning.
        """
        through_door = isinstance(agent, Ghost) and agent.may_pass_door
        maze = self.maze

        def passable(tile: Tile) -> bool:
            return maze.is_walkable(tile, through_door=through_door)

        return passable

    def wrap(self, tile: Tile) -> Tile:
        return self.maze.wrap(tile)

    # ── The controller handshake ──────────────────────────────────────────────

    def request_direction(self, agent: Agent, direction: Direction) -> bool:
        """Commit *agent* to *direction* for one tile.

        Called by the behavior-tree action nodes and by the keyboard handler.
        Returns False when the move is blocked or the agent is mid-tile — a
        direction may only be chosen at a decision point.
        """
        if not agent.is_deciding or not direction.is_moving:
            return False
        through_door = isinstance(agent, Ghost) and agent.may_pass_door
        return agent.face(direction, self.maze, through_door=through_door)

    # ── Main update ───────────────────────────────────────────────────────────

    def update(self, dt: float) -> List[GameEvent]:
        """Advance the world by *dt* seconds; returns the events it produced."""
        self.events = []
        if self.paused:
            return self.events

        self.elapsed += dt

        if self.phase in (GamePhase.READY, GamePhase.DYING, GamePhase.LEVEL_CLEAR):
            self.phase_timer -= dt
            if self.phase_timer <= 0.0:
                self._end_phase()
            return self.events

        if self.phase is not GamePhase.PLAYING:
            return self.events

        self._update_modes(dt)
        self._update_house(dt)
        self._move_agents(dt)
        self._consume_pellets()
        self._resolve_collisions()
        self._check_level_clear()
        return self.events

    def _end_phase(self) -> None:
        if self.phase is GamePhase.READY:
            self.phase = GamePhase.PLAYING
        elif self.phase is GamePhase.DYING:
            if self.lives <= 0:
                self.phase = GamePhase.GAME_OVER
                self._emit("game_over", "GAME OVER")
            else:
                self.reset_round()
        elif self.phase is GamePhase.LEVEL_CLEAR:
            self.start_level(self.level + 1)
            self._emit("level_start", f"LEVEL {self.level}")

    # ── Timers ────────────────────────────────────────────────────────────────

    def _update_modes(self, dt: float) -> None:
        """Run the power-pellet timer and the scatter/chase schedule."""
        if self.power_timer > 0.0:
            self.power_timer = max(0.0, self.power_timer - dt)
            blink = self.power_timer <= self.rules.power_blink_lead
            for ghost in self.ghosts:
                if ghost.is_edible:
                    ghost.blinking = blink and int(self.elapsed * 6) % 2 == 0
            if self.power_timer == 0.0:
                self._end_power()
            return

        # Scatter/chase only advances while no power pellet is active — the
        # arcade freezes the schedule for the duration of the fright.
        entry = self.rules.mode_schedule[self._mode_index]
        duration = float(entry[1])
        if duration < 0:
            return
        self._mode_timer += dt
        if self._mode_timer >= duration:
            self._mode_timer = 0.0
            self._mode_index = min(self._mode_index + 1, len(self.rules.mode_schedule) - 1)
            new_mode = GlobalMode(self.rules.mode_schedule[self._mode_index][0])
            if new_mode is not self.mode:
                self.mode = new_mode
                self._reverse_ghosts()
                self._emit("mode", new_mode.value.upper())

    def _update_house(self, dt: float) -> None:
        """Count down each penned ghost's release timer."""
        for ghost in self.ghosts:
            if ghost.state is not GhostState.IN_HOUSE:
                continue
            ghost.release_timer -= dt
            if ghost.release_timer <= 0.0:
                ghost.state = GhostState.LEAVING

    def _end_power(self) -> None:
        for ghost in self.ghosts:
            ghost.blinking = False
            if ghost.is_edible:
                ghost.state = GhostState.NORMAL
                ghost.speed = self._level_speed(self.rules.ghost_speed)
        self.ghost_chain_index = 0

    def _reverse_ghosts(self) -> None:
        """A mode change makes every ghost on the board turn around at once."""
        for ghost in self.ghosts:
            if ghost.state in (GhostState.NORMAL, GhostState.FRIGHTENED):
                ghost.last_direction = ghost.last_direction.opposite

    # ── Movement ──────────────────────────────────────────────────────────────

    def _move_agents(self, dt: float) -> None:
        self._apply_pacman_input()
        self.pacman.advance(dt * self._speed_factor(self.pacman), self.maze)
        for ghost in self.ghosts:
            ghost.advance(dt * self._speed_factor(ghost), self.maze)

    def _speed_factor(self, agent: Agent) -> float:
        """Per-agent speed multiplier: ghosts crawl through the tunnel."""
        if isinstance(agent, Ghost) and self.maze.is_tunnel(agent.tile):
            if agent.state is not GhostState.EATEN:
                return self.rules.tunnel_speed_factor
        return 1.0

    def _apply_pacman_input(self) -> None:
        """Turn the buffered player direction into an actual move.

        Pac-Man keeps going straight when the requested turn is into a wall,
        which is what makes holding a direction into a corner feel right.
        """
        pac = self.pacman
        if not pac.is_deciding:
            return
        if self.request_direction(pac, pac.desired_direction):
            return
        self.request_direction(pac, pac.last_direction)

    # ── Eating and dying ──────────────────────────────────────────────────────

    def _consume_pellets(self) -> None:
        tile = self.pacman.tile
        if tile in self.pellets:
            self.pellets.discard(tile)
            self._award(self.rules.points_pellet)
            self._emit("pellet", tile=tile, points=self.rules.points_pellet)
        elif tile in self.power_pellets:
            self.power_pellets.discard(tile)
            self._award(self.rules.points_power)
            self._emit("power", "POWER!", tile=tile, points=self.rules.points_power)
            self._start_power()

    def _start_power(self) -> None:
        self.power_timer = self.rules.power_duration
        self.ghost_chain_index = 0
        for ghost in self.ghosts:
            if ghost.state in (GhostState.NORMAL, GhostState.FRIGHTENED):
                ghost.state = GhostState.FRIGHTENED
                ghost.speed = self._level_speed(self.rules.frightened_speed)
                ghost.last_direction = ghost.last_direction.opposite
                ghost.blinking = False

    def _resolve_collisions(self) -> None:
        pac_pos = self.pacman.position
        for ghost in self.ghosts:
            if ghost.state is GhostState.EATEN:
                continue
            if euclidean(pac_pos, ghost.position) > COLLISION_RADIUS:
                continue
            if ghost.is_edible:
                self._eat_ghost(ghost)
            elif ghost.is_hostile:
                self._kill_pacman(ghost)
                return

    def _eat_ghost(self, ghost: Ghost) -> None:
        points = self.rules.ghost_chain[
            min(self.ghost_chain_index, len(self.rules.ghost_chain) - 1)
        ]
        self.ghost_chain_index += 1
        self._award(points)
        ghost.state = GhostState.EATEN
        ghost.blinking = False
        ghost.speed = self._level_speed(self.rules.eaten_speed)
        self._emit("ghost_eaten", f"{ghost.name.upper()} +{points}", ghost.tile, points)

    def _kill_pacman(self, ghost: Ghost) -> None:
        self.pacman.alive = False
        self.lives -= 1
        self.phase = GamePhase.DYING
        self.phase_timer = self.rules.dying_duration
        self._emit("pacman_caught", f"CAUGHT BY {ghost.name.upper()}", self.pacman.tile)

    def _check_level_clear(self) -> None:
        if self.pellets_left == 0:
            self.phase = GamePhase.LEVEL_CLEAR
            self.phase_timer = self.rules.level_clear_duration
            self._emit("level_clear", "LEVEL CLEARED")

    # ── Ghost house bookkeeping (called by the behavior trees) ────────────────

    def on_ghost_left_house(self, ghost: Ghost) -> None:
        """A ghost finished walking out of the door."""
        ghost.state = GhostState.FRIGHTENED if self.power_active else GhostState.NORMAL
        ghost.speed = self._level_speed(
            self.rules.frightened_speed if self.power_active else self.rules.ghost_speed
        )

    def on_ghost_reached_house(self, ghost: Ghost) -> None:
        """Eaten eyes made it back to the pen — respawn and queue a release."""
        ghost.state = GhostState.IN_HOUSE
        ghost.release_timer = 1.0
        ghost.speed = self._level_speed(self.rules.ghost_speed)
        ghost.blinking = False
        self._emit("ghost_home", f"{ghost.name.upper()} REVIVED", ghost.tile)

    # ── Scoring / events ──────────────────────────────────────────────────────

    def _award(self, points: int) -> None:
        self.score += points
        self.high_score = max(self.high_score, self.score)
        if self.rules.extra_life_at and self.score >= self._next_extra_life:
            self.lives += 1
            self._next_extra_life += self.rules.extra_life_at
            self._emit("extra_life", "EXTRA LIFE")

    def _emit(self, kind: str, message: str = "", tile: Optional[Tile] = None, points: int = 0) -> None:
        self.events.append(GameEvent(kind=kind, message=message, tile=tile, points=points))

    # ── Introspection helpers ─────────────────────────────────────────────────

    @property
    def is_over(self) -> bool:
        return self.phase is GamePhase.GAME_OVER

    def status_line(self) -> str:
        return (
            f"L{self.level} score={self.score} lives={self.lives} "
            f"pellets={self.pellets_left} mode={self.mode.value}"
            + (f" power={self.power_timer:.1f}s" if self.power_active else "")
        )

    def __repr__(self) -> str:  # noqa: D105
        return f"GameWorld({self.status_line()}, phase={self.phase.value})"
