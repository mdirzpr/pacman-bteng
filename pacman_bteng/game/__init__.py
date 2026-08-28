"""Pure game logic: maze, agents, pathfinding and the world state machine.

Nothing in this package imports BTEng or pygame — the game runs headless and
is driven from the outside.  :mod:`pacman_bteng.behaviors` plugs the behavior
trees in on top of it, :mod:`pacman_bteng.gui` draws it.
"""

from pacman_bteng.game.geometry import Direction, Tile, euclidean, manhattan
from pacman_bteng.game.maze import Maze
from pacman_bteng.game.entities import Agent, Ghost, GhostState, Pacman
from pacman_bteng.game.world import GamePhase, GameWorld, GlobalMode

__all__ = [
    "Direction",
    "Tile",
    "euclidean",
    "manhattan",
    "Maze",
    "Agent",
    "Ghost",
    "GhostState",
    "Pacman",
    "GameWorld",
    "GamePhase",
    "GlobalMode",
]
