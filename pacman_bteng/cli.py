"""Command line for ``python -m pacman_bteng``.

The YAML file is the reference for every tunable; these flags exist for the
handful you change while experimenting — which tree drives whom, how fast the
window runs, and where the introspection stream goes.  Anything passed here
wins over the file.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List, Optional

from pacman_bteng import __version__
from pacman_bteng.config import (
    DEFAULT_CONFIG_FILE,
    GHOST_NAMES,
    VISUAL_STYLES,
    ConfigError,
    Settings,
)

_EPILOG = """\
examples:
  python -m pacman_bteng                       play, four ghosts, four trees
  python -m pacman_bteng --auto                watch Pac-Man play himself
  python -m pacman_bteng --auto --loop         unattended demo, restarts itself
  python -m pacman_bteng --visuals deluxe      neon glow, particles, scanlines
  python -m pacman_bteng --ghosts blinky       one ghost, for reading the tree
  python -m pacman_bteng --debug               targets on screen, events on stdout
  python -m pacman_bteng --headless --max-seconds 20    smoke test, no window
  python -m pacman_bteng --list-nodes          every BT node and its ports

in-game keys:
  arrows / WASD  move        SPACE or P  pause      R  restart
  TAB  side panel            F1  debug overlay      F2  autopilot
  F3   lite / deluxe visuals                        ESC or Q  quit

live behavior-tree views (while a game is running):
  ./run.sh tree blinky       the tree, redrawn as it ticks
  ./run.sh viewer            a flat log of every transition
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pacman_bteng",
        description="Pac-Man with every ghost driven by a BTEng behavior tree.",
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    game = parser.add_argument_group("game")
    game.add_argument("--config", type=Path, default=None,
                      metavar="PATH", help=f"YAML settings (default: {DEFAULT_CONFIG_FILE})")
    game.add_argument("--maze", type=Path, default=None,
                      metavar="PATH", help="ASCII maze file to play")
    game.add_argument("--level", type=int, default=None,
                      help="start at this level (everyone moves faster each one)")
    game.add_argument("--ghosts", default=None, metavar="A,B",
                      help=f"which ghosts to spawn (default: {','.join(GHOST_NAMES)})")
    game.add_argument("--auto", action="store_true",
                      help="hand Pac-Man to his own behavior tree")
    game.add_argument("--seed", type=int, default=None,
                      help="seed the RNG so a run is reproducible")
    game.add_argument("--loop", action="store_true",
                      help="start a new game after GAME OVER (pairs with --auto)")

    window = parser.add_argument_group("window")
    window.add_argument("--fps", type=int, default=None, help="frame rate cap")
    window.add_argument("--tile", type=int, default=None, metavar="PX",
                        help="tile size; shrunk automatically to fit the screen")
    window.add_argument("--visuals", choices=VISUAL_STYLES, default=None,
                        help="lite = clean and cheap (default); deluxe = glow, "
                             "particles and scanlines. F3 toggles in game")
    window.add_argument("--no-panel", action="store_true",
                        help="hide the live behavior-tree side panel")
    window.add_argument("--debug", action="store_true",
                        help="draw each ghost's target and log game events")

    stream = parser.add_argument_group("introspection")
    stream.add_argument("--zmq-port", type=int, default=None,
                        help="port for the BTEng ZmqPublisher stream")
    stream.add_argument("--no-zmq", action="store_true",
                        help="do not publish the tick stream at all")
    stream.add_argument("--log-console", action="store_true",
                        help="print every node transition (very chatty)")
    stream.add_argument("--log-file", default=None, metavar="PATH",
                        help="append every node transition as JSON lines")

    headless = parser.add_argument_group("headless / tooling")
    headless.add_argument("--headless", action="store_true",
                          help="run with SDL's dummy driver and a fixed time step")
    headless.add_argument("--max-seconds", type=float, default=None, metavar="S",
                          help="stop after this much game time (0 = never)")
    headless.add_argument("--list-nodes", action="store_true",
                          help="print every registered BT node with its ports, then exit")
    headless.add_argument("--version", action="version", version=f"pacman-bteng {__version__}")

    return parser


def settings_from_args(args: argparse.Namespace) -> Settings:
    """Fold the CLI flags into the loaded configuration."""
    settings = Settings.load(args.config)

    if args.maze is not None:
        settings.maze_file = args.maze
    if args.level is not None:
        settings.level = max(1, args.level)
    if args.ghosts is not None:
        settings.ghosts = _parse_ghosts(args.ghosts)
    if args.seed is not None:
        settings.seed = args.seed
    settings.autopilot = args.auto
    settings.loop = args.loop
    settings.debug = args.debug
    settings.headless = args.headless
    if args.max_seconds is not None:
        settings.max_seconds = max(0.0, args.max_seconds)

    if args.fps is not None:
        settings.window.fps = max(1, args.fps)
    if args.tile is not None:
        settings.window.tile_size = max(6, args.tile)
    if args.visuals is not None:
        settings.window.visuals = args.visuals
    if args.no_panel:
        settings.window.show_panel = False

    if args.no_zmq:
        settings.introspection.enable_zmq = False
    if args.zmq_port is not None:
        settings.introspection.zmq_port = args.zmq_port
    if args.log_console:
        settings.introspection.console_log = True
    if args.log_file is not None:
        settings.introspection.log_file = args.log_file

    return settings


def _parse_ghosts(raw: str) -> List[str]:
    names = [part.strip().lower() for part in raw.split(",") if part.strip()]
    unknown = [n for n in names if n not in GHOST_NAMES]
    if unknown:
        raise ConfigError(
            f"unknown ghost(s) {', '.join(unknown)}; choose from {', '.join(GHOST_NAMES)}"
        )
    return names


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    # --list-nodes answers a question about the node library, not about a game,
    # so it must work with no display, no maze and no config file.
    if args.list_nodes:
        from pacman_bteng.behaviors import node_catalogue

        print(node_catalogue())
        return 0

    try:
        settings = settings_from_args(args)
    except ConfigError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return 2

    from pacman_bteng.app import run

    try:
        return run(settings)
    except FileNotFoundError as exc:
        print(f"missing file: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 - one readable line beats a traceback
        from pacman_bteng.behaviors import BrainError
        from pacman_bteng.game.maze import MazeError

        if isinstance(exc, (BrainError, MazeError, ConfigError)):
            print(f"error: {exc}", file=sys.stderr)
            return 2
        raise
