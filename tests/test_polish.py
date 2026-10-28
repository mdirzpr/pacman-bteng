"""Title screen, sound and overlay cards: the 1.1 presentation layer."""

from __future__ import annotations

import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")

from pacman_bteng.cli import build_parser, settings_from_args  # noqa: E402
from pacman_bteng.gui import audio  # noqa: E402
from pacman_bteng.gui.audio import SoundBoard  # noqa: E402
from pacman_bteng.gui.input import Intent, KEY_HINT  # noqa: E402


def test_every_game_event_has_a_sound():
    kinds = {"pellet", "power", "ghost_eaten", "pacman_caught", "level_start",
             "level_clear", "extra_life", "game_over"}
    assert kinds <= set(audio._effects())


def test_render_is_16_bit_pcm_of_the_right_length():
    pcm = audio._render([(440, 0.1), (0, 0.05)])
    assert len(pcm) == 2 * int(audio.SAMPLE_RATE * 0.1) + 2 * int(audio.SAMPLE_RATE * 0.05)


def test_disabled_soundboard_is_silent_and_safe():
    board = SoundBoard(enabled=False)
    board.play("pellet")
    assert board.toggle_mute() is True
    board.close()


def test_new_intents_are_documented_in_the_hint():
    assert Intent.TOGGLE_MUTE and Intent.START
    assert "M mute" in KEY_HINT


def test_cli_flags_fold_into_settings():
    args = build_parser().parse_args(["--mute", "--no-title"])
    settings = settings_from_args(args)
    assert settings.window.sound is False
    assert settings.window.title_screen is False


def test_title_card_only_for_interactive_runs():
    from pacman_bteng.app import PacmanApp

    for argv in (["--headless"], ["--headless", "--auto"]):
        app = PacmanApp(settings_from_args(build_parser().parse_args(argv)))
        try:
            assert app.title is False
            assert app.world.paused is False
        finally:
            app.renderer.close()


def _hud(world):
    from pacman_bteng.gui import Layout
    from pacman_bteng.gui.hud import Hud
    from pacman_bteng.gui.theme import Theme
    return Hud(Layout.compute(world.maze, 24, show_panel=True), Theme.default())


def test_score_sparkline_samples_once_per_second_and_resets_on_restart(world):
    hud = _hud(world)
    for step in range(40):
        world.elapsed = step * 0.25
        world.score = step * 10
        hud._sample_score(world)
    assert len(hud._score_marks) == 10          # 10 s of play, one mark per second
    world.elapsed = 0.0                          # restart
    hud._sample_score(world)
    assert hud._score_marks == [world.score]


def test_top_bar_draws_with_and_without_history(world):
    import pygame
    hud = _hud(world)
    surface = pygame.Surface((hud.layout.width, hud.layout.height))
    hud.draw_top_bar(surface, world)             # a single mark: no line yet
    for step in range(12):
        world.elapsed, world.score = float(step), step * 50
        hud.draw_top_bar(surface, world)


def test_tab_dock_hit_boxes_and_all_views_draw(world):
    import pygame
    from pacman_bteng.game.world import GameEvent
    hud = _hud(world)
    surface = pygame.Surface((hud.layout.width, hud.layout.height))
    assert hud.tab_at((0, 0)) is None            # nothing drawn yet
    world.elapsed = 5.0
    hud.record(world, [GameEvent("pellet"), GameEvent("power", "POWER!"),
                       GameEvent("ghost_eaten", "BLINKY +200")])
    for tab in ("trees", "stats", "log", "bogus"):
        hud.draw_panel(surface, world, (), tab=tab)
    assert [e[1] for e in hud._log] == ["power", "ghost_eaten"]   # pellets skipped
    for key, rect in hud._tab_rects.items():
        assert hud.tab_at(rect.center) == key
    world.elapsed = 0.0                           # restart clears the log
    hud.record(world, [])
    assert not hud._log


def test_tab_keys_and_clicks_become_intents(world):
    import pygame
    from pacman_bteng.gui.input import INTENT_BY_TAB, InputHandler, TAB_BY_INTENT
    assert set(INTENT_BY_TAB) == {"trees", "stats", "log"}
    pygame.display.init()
    pygame.display.set_mode((1, 1))
    handler = InputHandler()
    pygame.event.clear()
    pygame.event.post(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_2))
    pygame.event.post(pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=(5, 5)))
    got = handler.poll(world, hit_test=lambda pos: INTENT_BY_TAB["log"])
    assert [TAB_BY_INTENT[i] for i in got] == ["stats", "log"]


def test_card_lifts_only_for_a_close_hostile_ghost(world):
    from pacman_bteng.game.entities import GhostState
    hud = _hud(world)
    ghost = world.ghosts[0]
    ghost.state = GhostState.NORMAL
    far = hud._card_lift(world, "x", ghost)
    ghost.tile = world.pacman.tile
    close = hud._card_lift(world, "x", ghost)
    assert far == 0 and 0 < close <= hud.pad // 2          # never reaches the neighbour
    ghost.state = GhostState.FRIGHTENED                    # edible: harmless
    assert hud._card_lift(world, "x", ghost) == 0
    assert hud._card_lift(world, "pacman", None) == 0      # Pac-Man's own card


def test_deluxe_lift_eases_instead_of_jumping(world):
    from pacman_bteng.game.entities import GhostState
    from pacman_bteng.gui import Layout
    from pacman_bteng.gui.hud import Hud
    from pacman_bteng.gui.theme import Theme, VisualStyle
    hud = Hud(Layout.compute(world.maze, 24, show_panel=True),
              Theme.for_style(VisualStyle.DELUXE))
    ghost = world.ghosts[0]
    ghost.state = GhostState.NORMAL
    ghost.tile = world.pacman.tile
    hud._lift_dt = 1 / 60
    first = hud._card_lift(world, "g", ghost)
    for _ in range(60):
        last = hud._card_lift(world, "g", ghost)
    assert first < last == min(4, hud.pad // 2)


def test_tab_change_cross_fades_in_deluxe_only(world, monkeypatch):
    import pygame
    from pacman_bteng.gui import Layout
    from pacman_bteng.gui import hud as hud_mod
    from pacman_bteng.gui.theme import Theme, VisualStyle

    layout = Layout.compute(world.maze, 24, show_panel=True)
    surface = pygame.Surface((layout.width, layout.height))
    clock = [100.0]
    monkeypatch.setattr(hud_mod.time, "monotonic", lambda: clock[0])

    deluxe = hud_mod.Hud(layout, Theme.for_style(VisualStyle.DELUXE))
    deluxe.draw_panel(surface, world, (), tab="trees")
    assert deluxe._fade_from is None                    # first draw: nothing to fade from
    deluxe.draw_panel(surface, world, (), tab="stats")
    assert deluxe._fade_from is not None                # tab changed: old view is held
    clock[0] += hud_mod.TAB_FADE / 2
    deluxe.draw_panel(surface, world, (), tab="stats")
    assert deluxe._fade_from is not None                # still fading
    clock[0] += hud_mod.TAB_FADE
    deluxe.draw_panel(surface, world, (), tab="stats")
    assert deluxe._fade_from is None                    # done

    lite = hud_mod.Hud(layout, Theme.default())
    lite.draw_panel(surface, world, (), tab="trees")
    lite.draw_panel(surface, world, (), tab="log")
    assert lite._fade_from is None                      # the calm preset just switches


def test_pellet_ring_tracks_pellets_left_and_survives_a_small_window(world):
    import pygame
    from pacman_bteng.gui import Layout
    from pacman_bteng.gui.hud import Hud
    from pacman_bteng.gui.theme import Theme
    for tile in (24, 10):
        layout = Layout.compute(world.maze, tile, show_panel=True)
        hud = Hud(layout, Theme.default())
        surface = pygame.Surface((layout.width, layout.height))
        bar = pygame.Rect(0, 0, layout.maze_w, layout.top_bar)
        all_pellets = list(world.pellets)
        for keep in (len(all_pellets), len(all_pellets) // 2, 1, 0):
            world.pellets = set(all_pellets[:keep])
            surface.fill((0, 0, 0))
            x = hud._pellet_ring(surface, world, bar, bar.centery, 10)
            assert x > 10                      # always reports where it ended
