"""pygame front end: window, sprites, HUD and keyboard.

This package is the only one that knows what a pixel is.  It reads the world
and the behavior-tree brains, and it writes exactly one thing back —
``world.pacman.desired_direction`` when the player presses a key.  Everything
else it wants (pause, restart, autopilot) it hands upward as an
:class:`~pacman_bteng.gui.input.Intent` for :mod:`pacman_bteng.app` to decide.

Nothing here imports :mod:`pacman_bteng.behaviors`: the panel duck-types the
brain objects it is given, so the GUI keeps working with no trees attached and
the two packages never form an import cycle.

    from pacman_bteng.gui import Theme, VisualStyle, Layout, Renderer, Hud, \
                                InputHandler, Intent

The deluxe effect layer lives in :mod:`pacman_bteng.gui.effects` and is
imported by the Renderer only when that preset is selected, so the lite
preset never pays for a feature it does not draw.
"""

import os

# Set before any submodule imports pygame: the library reads this at import
# time, and its "Hello from the pygame community" banner would otherwise be the
# first thing every run prints — above our own start-up banner.
os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")

from pacman_bteng.gui.theme import Theme, VisualStyle
from pacman_bteng.gui.layout import Layout
from pacman_bteng.gui.hud import Hud
from pacman_bteng.gui.input import Intent, InputHandler
from pacman_bteng.gui.renderer import Renderer

__all__ = [
    "Theme",
    "VisualStyle",
    "Layout",
    "Hud",
    "Intent",
    "InputHandler",
    "Renderer",
]
