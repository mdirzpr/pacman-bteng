"""AgentBrain / BrainPool — turning an XML file into a ghost that thinks.

BTEng's ``TreeExecutor`` normally owns its own timing (``start_event_loop``),
but a game already has a clock: the frame.  So the executor is used in *manual*
mode — one ``tick_once()`` per rendered frame — which keeps the tree, the
physics and the picture on screen in exact lockstep and makes the whole thing
reproducible from a seed.

THE LOOPING-TREE PATTERN
------------------------
BTEng ships no ``Repeat`` decorator, and a tree that returns SUCCESS or FAILURE
is finished.  A ghost's brain must never be finished, so the runner does what
``turtlesim_bteng``'s controller does: when the root returns a terminal status
it resets every node to IDLE and keeps ticking.  One completed tree = one tile
of travel decided, which is exactly the arcade's own decision granularity.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Iterator, List, Optional, Sequence

from bteng import (
    Blackboard,
    ExecutorConfig,
    NodeFactory,
    NodeStatus,
    Tree,
    TreeExecutor,
    TreeMetadata,
    TreeNode,
    XMLTreeParser,
)

from pacman_bteng.behaviors import actions as _actions  # noqa: F401 - registers nodes
from pacman_bteng.behaviors import blackboard_keys as K
from pacman_bteng.behaviors import conditions as _conditions  # noqa: F401 - registers nodes
from pacman_bteng.game.entities import Agent, Ghost
from pacman_bteng.game.world import GameWorld

if TYPE_CHECKING:  # pragma: no cover - typing only
    from pacman_bteng.config import Settings
    from pacman_bteng.introspection.stream import BTIntrospection


class BrainError(RuntimeError):
    """A tree could not be loaded — bad path, bad XML, or failed validation."""


class AgentBrain:
    """One agent, one behavior tree, one blackboard, one executor."""

    def __init__(
        self,
        agent: Agent,
        world: GameWorld,
        tree_path: Path,
        *,
        introspection: Optional["BTIntrospection"] = None,
    ) -> None:
        self.name = agent.name
        self.agent = agent
        self.world = world
        self.tree_path = Path(tree_path)

        if not self.tree_path.exists():
            raise BrainError(
                f"{self.name}: behavior tree not found at {self.tree_path}. "
                "Check behavior.trees in config/game.yaml."
            )

        # A private Blackboard, never Blackboard.create(): create() memoises by
        # name, so four ghosts asking for one would all share a single board and
        # overwrite each other's target every tick.
        self.blackboard = Blackboard(scope_name=f"bt.{self.name}")
        K.seed(self.blackboard, agent, world)

        try:
            root = XMLTreeParser().parse_file(
                str(self.tree_path), blackboard=self.blackboard
            )
        except Exception as exc:  # noqa: BLE001 - re-raised with the file name
            raise BrainError(f"{self.tree_path}: {exc}") from exc

        self.tree = Tree(
            TreeMetadata(
                id=f"{self.name}_brain",
                description=f"{self.name} behavior tree ({self.tree_path.name})",
            ),
            root,
            self.blackboard,
        )

        # halt_on_completion=False: this runner decides what "done" means, and
        # a halt between cycles would throw away the RUNNING mover mid-tile.
        self.executor = TreeExecutor(
            ExecutorConfig(tick_interval=0.0, halt_on_completion=False, thread_pool_size=0)
        )
        if introspection is not None:
            self.executor.set_inspector(introspection.inspector)

        try:
            self.executor.set_tree(self.tree)   # validates every port mapping
        except Exception as exc:  # noqa: BLE001 - re-raised with the file name
            raise BrainError(f"{self.tree_path}: {exc}") from exc

        self.status: NodeStatus = NodeStatus.IDLE
        self.active_node: str = ""
        self.branch: str = ""
        self.cycles: int = 0

    # ── Execution ─────────────────────────────────────────────────────────────

    def tick(self) -> NodeStatus:
        """Tick the tree once, restarting it when a decision cycle completes."""
        K.sync(self.blackboard, self.agent, self.world)

        self.status = self.executor.tick_once()
        self._read_active_path()

        if self.status is not NodeStatus.RUNNING:
            # One cycle = one tile decided. Reset to IDLE so the next frame
            # re-evaluates the whole tree from the top.
            self.executor.reset_tree()
            self.cycles += 1

        return self.status

    def _read_active_path(self) -> None:
        """Record which branch is live, for the GUI panel and ``describe()``.

        ``tip()`` walks to the deepest RUNNING node.  After a cycle finishes
        nothing is RUNNING, so the previous labels are kept rather than
        blanking the panel for one frame in six.
        """
        tip = self.tree.root.tip()
        if tip is not None:
            self.active_node = _short(tip.name)

        for child in self.tree.root.get_children():
            if child.status is NodeStatus.RUNNING:
                self.branch = _short(child.name)
                break

        ghost = self.agent if isinstance(self.agent, Ghost) else None
        if ghost is not None and not ghost.behavior:
            ghost.behavior = self.branch

    # ── Reporting ─────────────────────────────────────────────────────────────

    def describe(self) -> str:
        """One line: which branch, running which node."""
        if not self.branch and not self.active_node:
            return "idle"
        if self.branch and self.active_node and self.branch != self.active_node:
            return f"{self.branch} -> {self.active_node}"
        return self.branch or self.active_node

    @property
    def feedback(self) -> str:
        """The deepest RUNNING node's own explanation of what it is doing."""
        tip = self.tree.root.tip()
        return tip.feedback_message if tip is not None else ""

    def walk(self) -> Iterator[TreeNode]:
        """Depth-first walk of the tree — used by the tests and ``--list-nodes``."""
        stack: List[TreeNode] = [self.tree.root]
        while stack:
            node = stack.pop()
            yield node
            stack.extend(reversed(node.get_children()))

    def shutdown(self) -> None:
        self.executor.shutdown()

    def __repr__(self) -> str:  # noqa: D105
        return f"AgentBrain({self.name!r}, {self.tree_path.name}, {self.describe()})"


def _short(name: str) -> str:
    """Strip the ``Agent/`` prefix node names carry so the stream stays readable."""
    return name.rsplit("/", 1)[-1]


class BrainPool:
    """Every brain in the game, ticked together."""

    def __init__(
        self,
        world: GameWorld,
        settings: "Settings",
        *,
        introspection: Optional["BTIntrospection"] = None,
    ) -> None:
        self.world = world
        self.settings = settings
        self._introspection = introspection
        self.brains: List[AgentBrain] = []
        self._accumulator = 0.0
        tick_hz = float(settings.behavior.tick_hz or 0.0)
        #: 0 Hz means "tick with the frame", which is what you want: the tree
        #: then decides at exactly the rate the world moves.
        self._period = (1.0 / tick_hz) if tick_hz > 0 else 0.0

        for name in settings.active_agents():
            agent = world.pacman if name == "pacman" else world.ghost(name)
            if agent is None:
                continue
            self.brains.append(
                AgentBrain(
                    agent, world, settings.tree_path(name), introspection=introspection
                )
            )

    # ── Execution ─────────────────────────────────────────────────────────────

    def tick_all(self, dt: float) -> None:
        """Advance every brain.  Respects ``behavior.tick_hz`` when it is set."""
        if self._period <= 0.0:
            for brain in self.brains:
                brain.tick()
            return

        self._accumulator += dt
        # Bounded catch-up: a long frame (a window drag, a breakpoint) must not
        # make the ghosts sprint through a hundred queued decisions at once.
        steps = 0
        while self._accumulator >= self._period and steps < 4:
            for brain in self.brains:
                brain.tick()
            self._accumulator -= self._period
            steps += 1

    def reset_all(self) -> None:
        """Send every tree back to IDLE — used when the player restarts."""
        for brain in self.brains:
            brain.executor.reset_tree()
            brain.branch = ""
            brain.active_node = ""
            brain.status = NodeStatus.IDLE
        self._accumulator = 0.0

    # ── Runtime composition ───────────────────────────────────────────────────

    def set_autopilot(self, enabled: bool, settings: "Settings") -> bool:
        """Attach or detach Pac-Man's own tree while the game is running.

        Built on demand rather than up front: loading the autopilot tree costs
        an XML parse and a validation pass, and a player who never presses the
        key should not pay for it.
        """
        existing = self.by_name("pacman")
        if enabled and existing is None:
            brain = AgentBrain(
                self.world.pacman,
                self.world,
                settings.tree_path("pacman"),
                introspection=self._introspection,
            )
            self.brains.append(brain)
            return True
        if not enabled and existing is not None:
            existing.shutdown()
            self.brains.remove(existing)
            return True
        return False

    # ── Access ────────────────────────────────────────────────────────────────

    def by_name(self, name: str) -> Optional[AgentBrain]:
        for brain in self.brains:
            if brain.name == name:
                return brain
        return None

    def __iter__(self) -> Iterator[AgentBrain]:
        return iter(self.brains)

    def __len__(self) -> int:
        return len(self.brains)

    def shutdown(self) -> None:
        for brain in self.brains:
            brain.shutdown()


# ── Node catalogue (``python -m pacman_bteng --list-nodes``) ──────────────────

#: Everything BTEng registers itself; anything else in the factory is ours.
_BUILTIN_NODES = frozenset(
    {
        "Sequence", "Fallback", "Selector", "Parallel",
        "ReactiveSequence", "ReactiveFallback",
        "Inverter", "Retry", "Timeout", "RateController",
        "ForceSuccess", "ForceFailure",
        "AlwaysSuccess", "AlwaysFailure", "AlwaysRunning",
        "SetBlackboard", "CheckBlackboard", "SubTree",
    }
)


def registered_nodes() -> List[str]:
    """The Pac-Man node types currently registered with BTEng's factory."""
    factory = NodeFactory.get_instance()
    return sorted(n for n in factory.registered_names() if n not in _BUILTIN_NODES)


def node_catalogue() -> str:
    """A printable table of every custom node and its ports.

    This is the reference when writing a tree by hand: a port that is not listed
    here does not exist, and an input with no default is required.
    """
    factory = NodeFactory.get_instance()
    lines = ["Pac-Man behavior-tree nodes (tag name == class name):", ""]
    for name in registered_nodes():
        manifest = factory.manifest(name)
        if manifest is None:
            continue
        doc = (manifest.description or "").strip().splitlines()
        lines.append(f"  {name:<24s} [{manifest.node_type.value}]  {doc[0] if doc else ''}")
        for port in manifest.ports:
            required = " (required)" if port.is_input() and port.default is None else ""
            default = "" if port.default is None else f" = {port.default!r}"
            lines.append(
                f"      {port.direction.value:<6s} {port.name}{default}{required}"
                + (f"   {port.description}" if port.description else "")
            )
        lines.append("")
    return "\n".join(lines)
