"""The two visual presets: palettes, contrast, wiring and the effect layer.

These are contract tests, not screenshot tests. They pin the things that break
silently — a palette field that quietly disappears, a preset whose text stops
being readable, an effect layer that leaks work per frame — rather than trying
to assert what the game looks like.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")

from pacman_bteng.config import VISUAL_STYLES, Settings, WindowSettings  # noqa: E402
from pacman_bteng.gui.layout import Layout  # noqa: E402
from pacman_bteng.gui.theme import Theme, VisualStyle, blend  # noqa: E402

#: Every colour the GUI is entitled to read off a Theme.
COLOUR_FIELDS = (
    "background wall wall_edge wall_glow door pellet power_pellet pacman "
    "pacman_dim frightened frightened_blink ghost_eye ghost_pupil text "
    "text_bright text_dim panel_bg panel_card panel_border accent accent_dim "
    "danger success warning overlay_scrim grid_line"
).split()

#: Text/background pairs that a reader actually has to parse.
TEXT_PAIRS = (
    ("text", "panel_bg"),
    ("text", "background"),
    ("text", "panel_card"),
    ("text_bright", "panel_card"),
    ("text_dim", "panel_bg"),
    ("accent", "panel_bg"),
)


def _relative_luminance(colour) -> float:
    def channel(value: float) -> float:
        value /= 255.0
        return value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4

    r, g, b = colour[:3]
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def contrast_ratio(a, b) -> float:
    la, lb = _relative_luminance(a), _relative_luminance(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


# ── Palettes ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("style", list(VisualStyle))
def test_every_preset_defines_every_colour(style: VisualStyle) -> None:
    theme = Theme.for_style(style)
    for name in COLOUR_FIELDS:
        colour = getattr(theme, name)
        assert isinstance(colour, tuple) and len(colour) == 3, name
        assert all(0 <= c <= 255 for c in colour), name
    assert theme.style is style


@pytest.mark.parametrize("style", list(VisualStyle))
@pytest.mark.parametrize("fg,bg", TEXT_PAIRS)
def test_text_clears_wcag_aa(style: VisualStyle, fg: str, bg: str) -> None:
    # 4.5:1 is the AA threshold for body text. The panel is dense, small and
    # read while the game moves, so this is the one place the palette is not
    # allowed to be merely tasteful.
    theme = Theme.for_style(style)
    ratio = contrast_ratio(getattr(theme, fg), getattr(theme, bg))
    assert ratio >= 4.5, f"{style.value}: {fg} on {bg} is {ratio:.2f}:1"


@pytest.mark.parametrize("style", list(VisualStyle))
def test_the_four_ghosts_stay_distinguishable(style: VisualStyle) -> None:
    # The panel colour-codes every block off the ghost colour, so two ghosts
    # that look alike make the whole panel harder to read at a glance.
    theme = Theme.for_style(style)
    names = ("blinky", "pinky", "inky", "clyde")
    assert set(names) <= set(theme.ghosts)
    lums = sorted(_relative_luminance(theme.ghosts[n]) for n in names)
    assert len(set(theme.ghosts[n] for n in names)) == 4
    # Not colour alone: they must also separate on brightness.
    assert lums[-1] - lums[0] > 0.25


def test_default_is_the_lite_preset() -> None:
    assert Theme.default().style is VisualStyle.LITE
    assert Theme.default() == Theme.for_style(VisualStyle.LITE)


def test_lite_is_the_calm_preset_and_deluxe_is_not() -> None:
    lite, deluxe = Theme.for_style(VisualStyle.LITE), Theme.for_style(VisualStyle.DELUXE)
    assert lite.glow_strength == 0.0 and lite.scanlines is False
    assert deluxe.glow_strength > 0.0 and deluxe.scanlines is True


def test_agent_colour_falls_back_to_pacman() -> None:
    theme = Theme.default()
    assert theme.agent_colour("blinky") == theme.ghosts["blinky"]
    assert theme.agent_colour("pacman") == theme.pacman
    assert theme.status_colour("NOT_A_STATUS") == theme.text_dim


def test_blend_is_a_linear_mix() -> None:
    assert blend((0, 0, 0), (100, 200, 40), 0.0) == (0, 0, 0)
    assert blend((0, 0, 0), (100, 200, 40), 1.0) == (100, 200, 40)


# ── Layout additions (the old API must survive) ───────────────────────────────

def test_layout_keeps_its_api_and_gains_a_scale(maze) -> None:
    layout = Layout.compute(maze, 24, show_panel=True)
    for attr in ("tile", "cols", "rows", "top_bar", "bottom_bar", "panel_w",
                 "width", "height", "size", "maze_w", "maze_h", "maze_origin",
                 "panel_x", "bottom_bar_y", "pad", "gutter"):
        assert hasattr(layout, attr), attr
    sizes = {r: layout.font_size(r) for r in
             ("small", "label", "value", "title", "overlay")}
    assert sizes["small"] < sizes["label"] < sizes["value"] < sizes["title"]
    assert sizes["overlay"] > sizes["title"]
    # An unknown role must not raise mid-frame.
    assert layout.font_size("nonsense") == sizes["label"]


# ── Settings / CLI wiring ─────────────────────────────────────────────────────

def test_visuals_setting_resolves_and_tolerates_nonsense() -> None:
    assert WindowSettings().visuals == "lite"
    assert WindowSettings(visuals="deluxe").visual_style() is VisualStyle.DELUXE
    # A typo in a config file must not cost you the game.
    assert WindowSettings(visuals="delux").visual_style() is VisualStyle.LITE
    assert set(VISUAL_STYLES) == {s.value for s in VisualStyle}


def test_cli_visuals_flag_overrides_the_config() -> None:
    from pacman_bteng import cli

    args = cli.build_parser().parse_args(["--visuals", "deluxe"])
    assert cli.settings_from_args(args).window.visuals == "deluxe"
    assert Settings.load().window.visuals in VISUAL_STYLES


# ── Renderer + effect layer ───────────────────────────────────────────────────

@pytest.fixture
def layout(maze) -> Layout:
    return Layout.compute(maze, 16, show_panel=True)


@pytest.mark.parametrize("style", list(VisualStyle))
def test_renderer_draws_a_frame_in_either_preset(style, layout, world) -> None:
    from pacman_bteng.gui import Renderer

    renderer = Renderer(layout, Theme.for_style(style), headless=True, style=style)
    try:
        # Only deluxe pays for an effect layer; lite must not build one at all.
        assert (renderer.effects is not None) is (style is VisualStyle.DELUXE)
        for i in range(30):
            events = world.update(1 / 60)
            renderer.draw(world, (), debug=(i % 5 == 0), overlay="READY!",
                          events=events)
        renderer.reset_effects()
    finally:
        renderer.close()


def test_the_board_cache_rebuilds_when_a_pellet_goes(layout, world) -> None:
    # The board is cached on the pellet count; if that key stopped changing,
    # eaten pellets would stay on screen forever.
    from pacman_bteng.gui import Renderer

    renderer = Renderer(layout, Theme.default(), headless=True)
    try:
        renderer.draw(world, ())
        first = renderer._board_key
        world.pellets.discard(next(iter(world.pellets)))
        renderer.draw(world, ())
        assert renderer._board_key != first
    finally:
        renderer.close()


def test_effect_layer_lives_and_dies(layout, world) -> None:
    import pygame

    from pacman_bteng.game.world import GameEvent
    from pacman_bteng.gui.effects import EffectLayer

    # The layer renders score popups, so it needs the font subsystem — which the
    # Renderer normally brings up before constructing it.
    pygame.init()
    pygame.font.init()

    fx = EffectLayer(layout, Theme.for_style(VisualStyle.DELUXE))
    assert fx.live == 0

    # Feed it a real event and it should have something to draw...
    fx.feed(world, [GameEvent(kind="ghost_eaten", message="BLINKY +200",
                              tile=world.ghosts[0].tile, points=200)])
    assert fx.live > 0

    # ...and it must clean up after itself rather than growing forever.
    for _ in range(600):
        fx.update(1 / 60)
    assert fx.live == 0

    fx.feed(world, [GameEvent(kind="pacman_caught", tile=world.pacman.tile)])
    fx.reset()
    assert fx.live == 0
    assert fx.shake() == (0, 0)


# ── Keyboard ──────────────────────────────────────────────────────────────────

def _keydown(key):
    import pygame

    return pygame.event.Event(pygame.KEYDOWN, {"key": key, "mod": 0, "unicode": ""})


def test_movement_keys_buffer_a_direction_rather_than_applying_it(world) -> None:
    # The handler's whole contract: it writes a *request*, and the world turns
    # that into a move only at a tile centre. If it ever moved Pac-Man itself,
    # the decision-point contract would have two owners.
    import pygame

    from pacman_bteng.game.geometry import Direction
    from pacman_bteng.gui.input import InputHandler

    pygame.init()
    handler = InputHandler()
    before = world.pacman.tile

    for key, expected in ((pygame.K_LEFT, Direction.LEFT), (pygame.K_w, Direction.UP),
                          (pygame.K_d, Direction.RIGHT)):
        pygame.event.post(_keydown(key))
        assert handler.poll(world) == []
        assert world.pacman.desired_direction is expected
    assert world.pacman.tile == before


def test_every_documented_key_maps_to_its_intent(world) -> None:
    import pygame

    from pacman_bteng.gui.input import KEY_HINT, InputHandler, Intent

    pygame.init()
    handler = InputHandler()
    expected = {
        pygame.K_ESCAPE: Intent.QUIT,
        pygame.K_q: Intent.QUIT,
        pygame.K_SPACE: Intent.PAUSE,
        pygame.K_p: Intent.PAUSE,
        pygame.K_r: Intent.RESTART,
        pygame.K_TAB: Intent.TOGGLE_PANEL,
        pygame.K_F1: Intent.TOGGLE_DEBUG,
        pygame.K_F2: Intent.TOGGLE_AUTOPILOT,
        pygame.K_F3: Intent.TOGGLE_VISUALS,
    }
    for key, intent in expected.items():
        pygame.event.post(_keydown(key))
        assert handler.poll(world) == [intent], intent

    # The on-screen hint sits next to the key map so the two cannot drift; if a
    # key is rebound and the hint is not, this is what notices.
    for token in ("SPACE", "R ", "TAB", "F1", "F2", "F3", "ESC"):
        assert token in KEY_HINT


def test_a_window_close_is_a_quit(world) -> None:
    import pygame

    from pacman_bteng.gui.input import InputHandler, Intent

    pygame.init()
    pygame.event.post(pygame.event.Event(pygame.QUIT))
    assert InputHandler().poll(world) == [Intent.QUIT]
