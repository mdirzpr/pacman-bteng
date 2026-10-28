"""pacman-bteng — the arcade Pac-Man, with every ghost driven by a BTEng
behavior tree and a pygame GUI to watch it happen.

Layered on purpose, and each layer is a package of its own:

    pacman_bteng.game     pure rules — maze, agents, pathfinding, world state
    pacman_bteng.behaviors  the BTEng node library + the per-agent tree runners
    pacman_bteng.gui      pygame rendering, HUD and keyboard input
    pacman_bteng.introspection  Inspector / Logger / ZmqPublisher wiring

``game`` knows nothing about BTEng or pygame, ``behaviors`` knows nothing about
pygame, and only :mod:`pacman_bteng.app` wires the three together.
"""

__all__ = ["__version__"]

__version__ = "1.1.0"
