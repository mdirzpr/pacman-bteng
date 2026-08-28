"""The command line and the settings it folds over ``config/game.yaml``.

The contract these tests pin down is "flags win over the file, and the file
wins over the dataclass defaults" — plus the two behaviors that decide whether
a mistyped run is friendly or fatal: an unknown ghost name is an error the user
sees, an unknown key in a config file is not.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pacman_bteng import cli
from pacman_bteng.config import (
    DEFAULT_BT_DIR,
    DEFAULT_CONFIG_FILE,
    DEFAULT_MAZE_FILE,
    GHOST_NAMES,
    ConfigError,
    Settings,
)


def parse(*argv: str):
    return cli.build_parser().parse_args(list(argv))


def settings_for(*argv: str) -> Settings:
    return cli.settings_from_args(parse(*argv))


# ── The parser ────────────────────────────────────────────────────────────────

def test_no_arguments_is_a_complete_run() -> None:
    args = parse()
    assert args.config is None and args.ghosts is None
    assert args.auto is False and args.headless is False
    assert args.list_nodes is False


def test_every_documented_flag_is_accepted() -> None:
    args = parse(
        "--config", "config/game.yaml",
        "--maze", "assets/mazes/classic.txt",
        "--level", "3",
        "--ghosts", "blinky,pinky",
        "--auto",
        "--seed", "99",
        "--fps", "30",
        "--tile", "16",
        "--no-panel",
        "--debug",
        "--zmq-port", "3000",
        "--no-zmq",
        "--log-console",
        "--log-file", "logs/run.jsonl",
        "--headless",
        "--max-seconds", "12.5",
    )
    assert args.config == Path("config/game.yaml")
    assert args.maze == Path("assets/mazes/classic.txt")
    assert (args.level, args.seed, args.fps, args.tile) == (3, 99, 30, 16)
    assert args.ghosts == "blinky,pinky"
    assert args.auto and args.no_panel and args.debug and args.headless
    assert args.zmq_port == 3000 and args.no_zmq and args.log_console
    assert args.log_file == "logs/run.jsonl"
    assert args.max_seconds == 12.5


def test_version_exits_zero() -> None:
    with pytest.raises(SystemExit) as exit_info:
        parse("--version")
    assert exit_info.value.code == 0


def test_an_unknown_flag_exits_with_usage() -> None:
    with pytest.raises(SystemExit) as exit_info:
        parse("--turbo")
    assert exit_info.value.code == 2


# ── Folding the flags over the file ───────────────────────────────────────────

def test_flags_override_the_yaml() -> None:
    plain = settings_for()
    tuned = settings_for("--tile", "12", "--level", "4", "--seed", "7", "--auto")

    assert tuned.window.tile_size == 12
    assert tuned.level == 4
    assert tuned.seed == 7
    assert tuned.autopilot is True
    # ...and the flags that were not passed still carry the file's values.
    assert tuned.window.fps == plain.window.fps
    assert tuned.rules.power_duration == plain.rules.power_duration


def test_no_zmq_turns_the_stream_off_without_touching_the_port() -> None:
    """The one flag every test run and every CI job needs.

    ``--no-zmq`` has to be enough on its own: the port stays configured so that
    dropping the flag turns the stream back on unchanged.
    """
    on = settings_for()
    off = settings_for("--no-zmq")

    assert on.introspection.enable_zmq is True
    assert off.introspection.enable_zmq is False
    assert off.introspection.zmq_port == on.introspection.zmq_port


def test_the_tile_size_has_a_floor() -> None:
    # A three-pixel tile renders a maze nobody can read; clamping beats a
    # window you have to kill from another terminal.
    assert settings_for("--tile", "1").window.tile_size == 6
    assert settings_for("--level", "-5").level == 1
    assert settings_for("--max-seconds", "-3").max_seconds == 0.0


def test_ghosts_are_parsed_case_insensitively_and_in_order() -> None:
    settings = settings_for("--ghosts", " Pinky , BLINKY ")
    assert settings.ghosts == ["pinky", "blinky"]
    assert settings.active_agents() == ["pinky", "blinky"]


def test_an_unknown_ghost_name_is_a_config_error() -> None:
    with pytest.raises(ConfigError, match="unknown ghost"):
        settings_for("--ghosts", "blinky,sue")


def test_main_turns_a_config_error_into_exit_code_two(capsys) -> None:
    assert cli.main(["--ghosts", "sue"]) == 2
    assert "configuration error" in capsys.readouterr().err


def test_autopilot_adds_pacman_to_the_active_agents() -> None:
    assert "pacman" not in settings_for().active_agents()
    assert settings_for("--auto").active_agents()[-1] == "pacman"


# ── --list-nodes ──────────────────────────────────────────────────────────────

def test_list_nodes_prints_the_catalogue_and_returns_zero(capsys) -> None:
    """It answers a question about the library, not about a game.

    So it must work with no display, no maze and no config file — which is why
    ``main`` handles it before it ever calls ``settings_from_args``.
    """
    assert cli.main(["--list-nodes"]) == 0

    out = capsys.readouterr().out
    for node in ("SetChaseTarget", "SetAmbushTarget", "StepTowardTarget", "IsFrightened"):
        assert node in out
    assert "pathfinder" in out          # ports are listed, not just names
    assert "target_x" in out


# ── Settings.load ─────────────────────────────────────────────────────────────

def test_the_shipped_config_produces_the_documented_values() -> None:
    settings = Settings.load()

    assert settings.window.tile_size == 24
    assert settings.window.fps == 60
    assert settings.window.show_panel is True

    assert settings.rules.lives == 3
    assert settings.rules.pacman_speed == 8.0
    assert settings.rules.ghost_speed == 7.4
    assert settings.rules.power_duration == 7.0
    assert settings.rules.ghost_chain == (200, 400, 800, 1600)
    assert settings.rules.release_delays == {
        "blinky": 0.0, "pinky": 2.0, "inky": 6.0, "clyde": 10.0
    }
    assert settings.rules.scatter_corners["blinky"] == (26, 1)
    assert settings.rules.mode_schedule[0] == ("scatter", 7.0)
    assert settings.rules.mode_schedule[-1] == ("chase", -1.0)

    assert settings.behavior.tick_hz == 0.0
    assert settings.behavior.trees["inky"] == "ghost_inky.xml"
    assert settings.behavior.pacman_auto == "pacman_auto.xml"
    assert settings.introspection.enable_zmq is True
    assert settings.introspection.zmq_port == 2667


def test_the_default_paths_point_at_the_repo() -> None:
    settings = Settings.load()
    assert settings.maze_file == DEFAULT_MAZE_FILE and settings.maze_file.exists()
    assert settings.bt_dir == DEFAULT_BT_DIR and settings.bt_dir.is_dir()
    assert DEFAULT_CONFIG_FILE.exists()


def test_tree_path_resolves_relative_names_against_the_bt_dir() -> None:
    settings = Settings.load()
    for ghost in GHOST_NAMES:
        assert settings.tree_path(ghost) == DEFAULT_BT_DIR / f"ghost_{ghost}.xml"
        assert settings.tree_path(ghost).exists()
    assert settings.tree_path("pacman") == DEFAULT_BT_DIR / "pacman_auto.xml"


def test_tree_path_leaves_an_absolute_name_alone(tmp_path) -> None:
    settings = Settings.load()
    settings.behavior.trees["blinky"] = str(tmp_path / "custom.xml")
    assert settings.tree_path("blinky") == tmp_path / "custom.xml"


def test_an_unknown_key_in_a_config_file_is_ignored(tmp_path) -> None:
    """A config written for a newer build must still start an older one.

    Unknown keys are skipped rather than fatal — the alternative is that adding
    one tunable breaks every checkout that has not pulled yet.
    """
    path = tmp_path / "game.yaml"
    path.write_text(
        "window:\n"
        "  tile_size: 18\n"
        "  hyperdrive: true\n"          # not a field on WindowSettings
        "rules:\n"
        "  lives: 5\n"
        "unheard_of_section:\n"
        "  whatever: 1\n",
        encoding="utf-8",
    )

    settings = Settings.load(path)

    assert settings.window.tile_size == 18
    assert settings.rules.lives == 5
    assert not hasattr(settings.window, "hyperdrive")


def test_an_empty_config_file_falls_back_to_the_defaults(tmp_path) -> None:
    path = tmp_path / "empty.yaml"
    path.write_text("", encoding="utf-8")
    assert Settings.load(path).window.tile_size == 24


def test_a_missing_explicit_config_file_is_an_error(tmp_path) -> None:
    # Silently ignoring --config would run the game with settings the user
    # never asked for and give them no way to notice.
    with pytest.raises(ConfigError, match="not found"):
        Settings.load(tmp_path / "absent.yaml")


def test_a_config_file_that_is_not_a_mapping_is_an_error(tmp_path) -> None:
    path = tmp_path / "list.yaml"
    path.write_text("- one\n- two\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="mapping"):
        Settings.load(path)


def test_with_overrides_ignores_none(tmp_path) -> None:
    settings = Settings.load()
    same = settings.with_overrides(level=None, debug=True)
    assert same.level == settings.level
    assert same.debug is True


# ── --loop, the unattended-demo flag ──────────────────────────────────────────

def test_loop_defaults_off_and_the_flag_turns_it_on() -> None:
    # A player who reaches GAME OVER wants to see it, not to be dealt a new
    # board mid-thought; looping is opt-in for exactly that reason.
    assert Settings.load().loop is False
    assert cli.settings_from_args(cli.build_parser().parse_args([])).loop is False
    assert cli.settings_from_args(cli.build_parser().parse_args(["--auto", "--loop"])).loop is True
