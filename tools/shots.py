#!/usr/bin/env python3
"""
Regenerate the screenshots in ``docs/images/``.

    .venv/bin/python tools/shots.py           # all of them
    .venv/bin/python tools/shots.py deluxe    # just one
    ./run.sh shots                            # the lazy way

Runs the real game headlessly against SDL's dummy video driver, so it works
over ssh and in CI, and saves the frames the README links to.

Every shot is seeded and taken at a *described moment* rather than at a
wall-clock time: "the first frame where all four ghosts are out and scattering"
is reproducible, and it is also the frame that actually shows the thing the
caption claims.  The game is deterministic for a given seed, so re-running this
gives byte-identical images until the rendering itself changes -- which is the
point.  The docs should move when the pixels move, not because a different
afternoon happened to be running when somebody pressed a key.
"""
from __future__ import annotations

import os
import sys

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")

# Running a file inside tools/ puts tools/ on sys.path, not the repo root, so
# the package this script exists to photograph would not import.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pygame  # noqa: E402

from pacman_bteng.app import _TITLE_CARD                 # noqa: E402
from pacman_bteng.behaviors import BrainPool             # noqa: E402
from pacman_bteng.config import Settings                 # noqa: E402
from pacman_bteng.game import GameWorld, GhostState, Maze  # noqa: E402
from pacman_bteng.game.world import GlobalMode           # noqa: E402
from pacman_bteng.gui import Layout, Renderer            # noqa: E402
from pacman_bteng.gui.theme import Theme, VisualStyle    # noqa: E402

OUT_DIR = os.path.join("docs", "images")
DT = 1.0 / 60.0
TIMEOUT_SECONDS = 120.0


# ── The moments worth photographing ───────────────────────────────────────────

def all_ghosts_loose(world) -> bool:
    """Every ghost out of the pen and behaving normally."""
    return all(g.state is GhostState.NORMAL for g in world.ghosts)


def ghost_branches(brains) -> set:
    """The tree branch each ghost is currently running."""
    return {b.branch for b in brains if b.name != "pacman"}


def settled_on(brains, branch: str) -> bool:
    """True when every ghost is running *branch* and the panel says so.

    Photographing a coherent frame needs this, not just the world state.  The
    frame a power pellet expires on has every ghost already NORMAL while the
    tick that was just drawn still ran the flee branch, so the panel reads
    "Flee" over a board where nothing is frightened -- true, and unexplainable
    in a caption.
    """
    return ghost_branches(brains) == {branch}


def the_hunt(world, brains) -> bool:
    """All four ghosts loose and actively chasing: the game at full pressure.

    Four ghosts, four different targets, one node of difference between their
    trees -- the whole project in a still.
    """
    return (
        world.elapsed > 12.0
        and world.score > 0
        and world.mode is GlobalMode.CHASE
        and not world.power_active
        and all_ghosts_loose(world)
        and settled_on(brains, "Chase")
    )


def four_corners(world, brains) -> bool:
    """Scatter, with all four ghosts loose and aiming at their own corners.

    This is the shot that makes the project's argument in one picture: the four
    trees are identical except for one node, and here that one node is sending
    them to four different corners of the maze.

    The test is the targets themselves, not the mode flag.  Asking only for
    "mode is SCATTER" caught the frame a power pellet expired on, where the
    ghosts were already NORMAL but the tick just drawn had still run the flee
    branch -- a picture of four ghosts running away, captioned "four corners".
    """
    return (
        world.elapsed > 12.0
        and world.mode is GlobalMode.SCATTER
        and not world.power_active
        and all_ghosts_loose(world)
        and all(g.target == g.scatter_corner for g in world.ghosts)
        and settled_on(brains, "Scatter")
    )


def ghosts_frightened(world, brains) -> bool:
    """A power pellet burning, with the whole board running away."""
    return (
        world.power_active
        and sum(g.is_edible for g in world.ghosts) >= 3
        and world.elapsed > 8.0
    )


def ghost_closing_in(world, brains) -> bool:
    """A hostile ghost within 4 tiles of Pac-Man: its card is fully lifted."""
    from pacman_bteng.game.geometry import manhattan
    return world.elapsed > 8.0 and any(
        g.is_hostile and manhattan(g.tile, world.pacman.tile) <= 4
        for g in world.ghosts
    )


#: name -> how to take it
SHOTS = {
    "lite": dict(
        style=VisualStyle.LITE, until=the_hunt, debug=False, seed=1234, tile=24,
        caption="the lite preset, all four ghosts chasing",
    ),
    "deluxe": dict(
        style=VisualStyle.DELUXE, until=ghosts_frightened, debug=False, seed=1234,
        tile=24, caption="the deluxe preset, a power pellet burning",
    ),
    "debug": dict(
        style=VisualStyle.LITE, until=four_corners, debug=True, seed=1234, tile=24,
        caption="F1 debug overlay: four ghosts, four corners",
    ),
    "lift": dict(
        style=VisualStyle.DELUXE, until=ghost_closing_in, debug=False, seed=1234,
        tile=24, crop="panel", scale=2,
        caption="a ghost closing in lifts its card, deluxe preset",
    ),
    "stats": dict(
        style=VisualStyle.DELUXE, until=lambda world, brains: world.elapsed > 16.0,
        debug=False, seed=1234, tile=24, crop="panel", scale=2, panel_tab="stats",
        caption="the STATS tab of the side panel, deluxe preset",
    ),
    "log": dict(
        style=VisualStyle.LITE, until=lambda world, brains: world.elapsed > 16.0,
        debug=False, seed=1234, tile=24, crop="panel", scale=2, panel_tab="log",
        caption="the LOG tab of the side panel, lite preset",
    ),
    "title": dict(
        style=VisualStyle.DELUXE, until=lambda world, brains: world.elapsed > 1.0,
        debug=False, seed=1234, tile=24, overlay=_TITLE_CARD,
        caption="the title card, deluxe preset",
    ),
    "panel": dict(
        style=VisualStyle.DELUXE, until=the_hunt, debug=False, seed=1234, tile=24,
        crop="panel", scale=2, caption="the live behavior-tree panel, 2x",
    ),
}


# ── Taking one ────────────────────────────────────────────────────────────────

def shoot(name: str) -> str:
    spec = SHOTS[name]
    settings = Settings.load()
    settings.autopilot = True                 # nobody is holding the arrow keys
    settings.introspection.enable_zmq = False  # a screenshot needs no stream

    maze = Maze.from_file(settings.maze_file)
    world = GameWorld(maze, settings.rules, seed=spec["seed"])
    brains = BrainPool(world, settings)
    layout = Layout.compute(maze, spec["tile"], show_panel=True)
    renderer = Renderer(
        layout, Theme.for_style(spec["style"]),
        show_panel=True, headless=True, style=spec["style"],
    )

    try:
        frame = _run_until(world, brains, renderer, spec)
        surface = _crop(renderer.screen, layout, spec)
        path = os.path.join(OUT_DIR, f"{name}.png")
        pygame.image.save(surface, path)
    finally:
        renderer.close()

    print(f"{path:28s} t={frame * DT:5.1f}s  {world.status_line()}  # {spec['caption']}")
    return path


def _run_until(world, brains, renderer, spec) -> int:
    """Drive the real game until the moment arrives, drawing every frame.

    Every frame is drawn, not just the last one: the deluxe layer's particles
    and popups are stateful, so a still taken after one lone draw call would be
    a picture of an effect layer that had never seen the game happen.
    """
    until = spec["until"]
    limit = int(TIMEOUT_SECONDS / DT)
    for frame in range(limit):
        brains.tick_all(DT)
        events = world.update(DT)
        renderer.draw(world, brains.brains, debug=spec["debug"], events=events,
                      overlay=spec.get("overlay", ""),
                      panel_tab=spec.get("panel_tab", "trees"))
        if until(world, brains.brains):
            return frame
    raise SystemExit(
        f"shots: never reached the moment for {spec['caption']!r} "
        f"in {TIMEOUT_SECONDS:.0f}s of game time"
    )


def _crop(screen, layout, spec):
    """Whole window, or a scaled crop of one region of it."""
    if spec.get("crop") != "panel":
        return screen
    region = pygame.Rect(layout.panel_x, 0, layout.panel_w, layout.height)
    panel = screen.subsurface(region).copy()
    scale = spec.get("scale", 1)
    if scale == 1:
        return panel
    # Nearest-neighbour on purpose: the panel is text and hairlines, and
    # smoothscale would turn both to mush at 2x.
    return pygame.transform.scale(
        panel, (panel.get_width() * scale, panel.get_height() * scale)
    )


def main(argv: list[str]) -> int:
    names = argv[1:] or list(SHOTS)
    unknown = [n for n in names if n not in SHOTS]
    if unknown:
        print(f"unknown shot(s): {', '.join(unknown)}", file=sys.stderr)
        print(f"available: {', '.join(SHOTS)}", file=sys.stderr)
        return 2
    os.makedirs(OUT_DIR, exist_ok=True)
    for name in names:
        shoot(name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
