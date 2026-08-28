"""Settings — one typed object assembled from ``config/game.yaml`` + CLI flags.

The YAML file is the reference for what is tunable; the CLI overrides a handful
of the same values.  Everything downstream (the world, the brains, the GUI, the
introspection stream) reads its parameters from here and never from the file.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from pacman_bteng.game.world import Rules

#: ``pacman_bteng/`` — the package directory.
PACKAGE_ROOT = Path(__file__).resolve().parent
#: The repository root: where ``config/``, ``assets/`` and ``bt_structures/`` live.
PROJECT_ROOT = PACKAGE_ROOT.parent

DEFAULT_CONFIG_FILE = PROJECT_ROOT / "config" / "game.yaml"
DEFAULT_MAZE_FILE = PROJECT_ROOT / "assets" / "mazes" / "classic.txt"
DEFAULT_BT_DIR = PROJECT_ROOT / "bt_structures"

GHOST_NAMES: Tuple[str, ...] = ("blinky", "pinky", "inky", "clyde")


class ConfigError(ValueError):
    """The config file exists but could not be used."""


#: The two visual presets.  ``lite`` is the default and the calm one; ``deluxe``
#: adds the glow, particles and CRT treatment.
VISUAL_STYLES: Tuple[str, ...] = ("lite", "deluxe")


@dataclass
class WindowSettings:
    """Everything the pygame window needs to know."""

    tile_size: int = 24
    fps: int = 60
    show_panel: bool = True
    caption: str = "Pac-Man · BTEng"
    #: "lite" or "deluxe" — see VISUAL_STYLES.  Anything else falls back to
    #: "lite" rather than failing to start: a typo in a config file should not
    #: cost you the game.
    visuals: str = "lite"

    def visual_style(self):
        """The :class:`~pacman_bteng.gui.theme.VisualStyle` this names.

        Resolved here rather than stored as the enum so ``config.py`` stays
        importable with no pygame installed — the GUI is the only layer that
        needs the enum itself.
        """
        from pacman_bteng.gui.theme import VisualStyle

        try:
            return VisualStyle(str(self.visuals).strip().lower())
        except ValueError:
            return VisualStyle.LITE


@dataclass
class BehaviorSettings:
    """Which XML drives which agent, and how often the trees are ticked."""

    #: ``0`` means "tick once per rendered frame", which is what you want.
    tick_hz: float = 0.0
    trees: Dict[str, str] = field(
        default_factory=lambda: {name: f"ghost_{name}.xml" for name in GHOST_NAMES}
    )
    pacman_auto: str = "pacman_auto.xml"


@dataclass
class IntrospectionSettings:
    """Live BT event streaming — the equivalent of Groot2 for BTEng."""

    enable_zmq: bool = True
    zmq_port: int = 2667
    console_log: bool = False
    log_file: Optional[str] = None


@dataclass
class Settings:
    """The fully resolved configuration for one run."""

    maze_file: Path = DEFAULT_MAZE_FILE
    bt_dir: Path = DEFAULT_BT_DIR
    rules: Rules = field(default_factory=Rules)
    window: WindowSettings = field(default_factory=WindowSettings)
    behavior: BehaviorSettings = field(default_factory=BehaviorSettings)
    introspection: IntrospectionSettings = field(default_factory=IntrospectionSettings)

    ghosts: List[str] = field(default_factory=lambda: list(GHOST_NAMES))
    autopilot: bool = False
    level: int = 1
    debug: bool = False
    headless: bool = False
    #: Stop after this many seconds of game time (0 = run until quit).
    max_seconds: float = 0.0
    #: Start a fresh game a few seconds after GAME OVER.  Meant for --auto: an
    #: unattended demo that stops forever on its first death is not a demo.
    loop: bool = False
    seed: Optional[int] = None

    # ── Loading ───────────────────────────────────────────────────────────────

    @classmethod
    def load(cls, path: Optional[Path | str] = None) -> "Settings":
        """Build settings from a YAML file, falling back to the defaults.

        A missing default config file is not an error — the dataclass defaults
        are a complete, playable configuration on their own.
        """
        config_path = Path(path) if path else DEFAULT_CONFIG_FILE
        if not config_path.exists():
            if path is not None:
                raise ConfigError(f"config file not found: {config_path}")
            return cls()

        try:
            import yaml
        except ImportError as exc:  # pragma: no cover - dependency is declared
            raise ConfigError(
                "PyYAML is required to read a config file. "
                "Install it with: pip install -r requirements.txt"
            ) from exc

        try:
            raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            raise ConfigError(f"{config_path}: {exc}") from exc
        if not isinstance(raw, Mapping):
            raise ConfigError(f"{config_path}: top level must be a mapping")

        return cls(
            window=_build(WindowSettings, raw.get("window")),
            rules=_build_rules(raw.get("rules")),
            behavior=_build(BehaviorSettings, raw.get("behavior")),
            introspection=_build(IntrospectionSettings, raw.get("introspection")),
        )

    # ── Derived paths ─────────────────────────────────────────────────────────

    def tree_path(self, agent: str) -> Path:
        """Absolute path to the XML that drives *agent*.

        ``agent`` is a ghost name, or ``"pacman"`` for the autopilot tree.
        """
        name = (
            self.behavior.pacman_auto
            if agent == "pacman"
            else self.behavior.trees.get(agent, f"ghost_{agent}.xml")
        )
        candidate = Path(name)
        return candidate if candidate.is_absolute() else self.bt_dir / candidate

    def active_agents(self) -> List[str]:
        """Every agent that gets a behavior tree this run."""
        return ([*self.ghosts, "pacman"] if self.autopilot else list(self.ghosts))

    def with_overrides(self, **kwargs: Any) -> "Settings":
        """Return a copy with the given top-level fields replaced."""
        return replace(self, **{k: v for k, v in kwargs.items() if v is not None})


# ── YAML → dataclass helpers ──────────────────────────────────────────────────

def _build(cls: type, raw: Any):
    """Instantiate *cls* from a mapping, ignoring keys it does not declare.

    Unknown keys are skipped rather than fatal: a config written for a newer
    version should still start an older build.
    """
    if not isinstance(raw, Mapping):
        return cls()
    fields = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
    return cls(**{k: v for k, v in raw.items() if k in fields})


def _build_rules(raw: Any) -> Rules:
    """Rules need two YAML shapes normalised: mode pairs and corner lists."""
    rules = _build(Rules, raw)
    if isinstance(raw, Mapping):
        if "mode_schedule" in raw:
            rules.mode_schedule = tuple(
                (str(mode), float(duration)) for mode, duration in raw["mode_schedule"]
            )
        if "scatter_corners" in raw:
            rules.scatter_corners = {
                str(name): (int(tile[0]), int(tile[1]))
                for name, tile in raw["scatter_corners"].items()
            }
        if "ghost_chain" in raw:
            rules.ghost_chain = tuple(int(v) for v in raw["ghost_chain"])
        if "release_delays" in raw:
            rules.release_delays = {
                str(name): float(value) for name, value in raw["release_delays"].items()
            }
    return rules
