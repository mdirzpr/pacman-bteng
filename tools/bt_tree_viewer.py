#!/usr/bin/env python3
"""Live *visual* tree view for the BTEng ZmqPublisher tick stream.

Where ``bt_zmq_viewer.py`` prints a flat scrolling log, this draws the actual
behavior-tree structure (parsed from the agent's XML in ``bt_structures/``) and
recolors each node in place as tick events arrive on ``tcp://localhost:2667`` —
a lightweight, terminal-only alternative to Groot2 (which is incompatible with
BTEng).

    ./.venv/bin/python tools/bt_tree_viewer.py                 # Blinky
    ./.venv/bin/python tools/bt_tree_viewer.py --agent clyde
    ./.venv/bin/python tools/bt_tree_viewer.py --xml /path/to/tree.xml

or, the lazy way:  ``./run.sh tree clyde``.

ONE STREAM, FIVE TREES
----------------------
The game ticks four ghost trees plus (with ``--auto``) Pac-Man's, and they all
publish onto the same socket.  A viewer showing one tree therefore has to decide
which events are *its*.  It does that by node name: every instance name in the
XML is prefixed with its agent (``name="Blinky/Chase"``), so an event whose
prefix is not ours is another agent's business and is counted, not drawn.  The
prefix is stripped from the drawn labels — it is the same word in the header.

Needs two small pure-Python deps on the host:  pip install rich pyzmq
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import xml.etree.ElementTree as ET
from collections import deque
from pathlib import Path

try:
    import zmq
except ImportError:
    sys.exit("pyzmq is not installed. Install it with:  pip install rich pyzmq")

try:
    from rich.console import Group
    from rich.live import Live
    from rich.text import Text
    from rich.tree import Tree as RichTree
except ImportError:
    sys.exit("rich is not installed. Install it with:  pip install rich pyzmq")

DEFAULT_HOST = "localhost"
DEFAULT_PORT = 2667
TOPIC = b"bteng"

# Repo layout: this file lives in <repo>/tools/, the trees in <repo>/bt_structures/.
REPO_ROOT = Path(__file__).resolve().parent.parent
BT_STRUCTURES = REPO_ROOT / "bt_structures"

#: agent -> XML basename, mirroring ``BehaviorSettings`` in pacman_bteng/config.py.
#: Hard-coded rather than imported: this stays a standalone host tool that runs
#: even when the package cannot (a half-finished checkout, a different venv).
AGENT_TREES = {
    "blinky": "ghost_blinky.xml",
    "pinky": "ghost_pinky.xml",
    "inky": "ghost_inky.xml",
    "clyde": "ghost_clyde.xml",
    "pacman": "pacman_auto.xml",
}

# Tags that are tree wiring / metadata, not tickable nodes.
_SKIP_TAGS = {"TreeNodesModel"}

# status -> (rich style, glyph)
STATUS_STYLE = {
    "SUCCESS": ("green", "●"),
    "FAILURE": ("red", "●"),
    "RUNNING": ("yellow", "●"),
    "SKIPPED": ("cyan", "◌"),
    "IDLE": ("grey50", "○"),
}
IDLE_THRESHOLD = 2.0  # seconds without events -> header shows "idle"


class Node:
    """One tickable BT node: static identity from XML + live state from ZMQ."""

    __slots__ = ("tag", "name", "children", "status", "dur_ms", "ticks", "last_ts")

    def __init__(self, tag: str, name: str) -> None:
        self.tag = tag
        self.name = name or tag
        self.children: list[Node] = []
        self.status = "IDLE"
        self.dur_ms = 0.0
        self.ticks = 0
        self.last_ts = 0.0


class _HelpFormatter(
    argparse.ArgumentDefaultsHelpFormatter, argparse.RawDescriptionHelpFormatter
):
    """Show defaults in the option list, keep the epilog's line breaks."""


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Live visual view of one agent's BTEng behavior tree.",
        formatter_class=_HelpFormatter,
        epilog=(
            "Examples:\n"
            "  %(prog)s --agent pinky\n"
            "  %(prog)s --agent pacman        # only ticks when the game runs --auto\n"
            "  %(prog)s --xml bt_structures/ghost_inky.xml\n"
        ),
    )
    parser.add_argument("--host", default=DEFAULT_HOST, help="ZmqPublisher host.")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="ZmqPublisher port.")
    parser.add_argument(
        "--agent",
        choices=tuple(AGENT_TREES),
        default="blinky",
        help="Which agent's tree to draw the skeleton from.",
    )
    parser.add_argument(
        "--xml",
        type=Path,
        default=None,
        help="Explicit path to a behavior-tree XML (overrides --agent).",
    )
    return parser.parse_args(argv)


# ── XML skeleton ──────────────────────────────────────────────────────────────

def _build_node(elem: ET.Element) -> Node | None:
    """Recursively convert an XML element into a Node, skipping wiring tags."""
    if elem.tag in _SKIP_TAGS:
        return None
    node = Node(elem.tag, elem.attrib.get("name", ""))
    for child in elem:
        sub = _build_node(child)
        if sub is not None:
            node.children.append(sub)
    return node


def load_skeleton(xml_path: Path) -> Node:
    """Parse the BTEng XML and return the root of the tickable tree.

    Only the ``<Tree>`` element is structural; ``<TreeNodesModel>`` is a manifest
    of node *types* and would otherwise show up as a phantom subtree.
    """
    root_elem = ET.parse(xml_path).getroot()
    # Find the <Tree> the game executes (main_tree_to_execute), else the first.
    main_id = root_elem.attrib.get("main_tree_to_execute")
    tree_elem = None
    for tree in root_elem.findall("Tree"):
        if main_id is None or tree.attrib.get("ID") == main_id:
            tree_elem = tree
            break
    if tree_elem is None:
        tree_elem = root_elem.find("Tree")
    if tree_elem is None:
        tree_elem = root_elem
    # The tickable root is the Tree's single child (the ReactiveFallback brain).
    children = [c for c in tree_elem if c.tag not in _SKIP_TAGS]
    if len(children) == 1:
        built = _build_node(children[0])
        if built is not None:
            return built
    # Defensive: wrap multiple/zero children under a synthetic root.
    root = Node("Tree", tree_elem.attrib.get("ID", "root"))
    for child in children:
        sub = _build_node(child)
        if sub is not None:
            root.children.append(sub)
    return root


def build_lookup(node: Node, by_name: dict[str, Node], by_tag: dict[str, Node]) -> None:
    """Index the skeleton by instance name (lowercased) and by class tag.

    Two indexes because events may carry either: BTEng reports the instance name
    when the XML gave one, and falls back to the node type when it did not.
    Names are matched case-insensitively so a stray ``blinky/Chase`` still lands.
    """
    by_name.setdefault(node.name.lower(), node)
    by_tag.setdefault(node.tag, node)  # first occurrence wins
    for child in node.children:
        build_lookup(child, by_name, by_tag)


def detect_prefix(node: Node, fallback: str) -> str:
    """The agent prefix actually used inside the XML, e.g. ``Blinky``.

    Read from the tree rather than assumed from ``--agent`` so that an explicit
    ``--xml`` (or a differently-capitalised name) still routes correctly.
    """
    if "/" in node.name:
        return node.name.split("/", 1)[0]
    for child in node.children:
        found = detect_prefix(child, "")
        if found:
            return found
    return fallback


def strip_prefix(name: str, prefix: str) -> str:
    """``Blinky/Chase`` -> ``Chase``; anything else is returned untouched."""
    head, sep, tail = name.partition("/")
    return tail if sep and head.lower() == prefix.lower() else name


# ── Rendering ─────────────────────────────────────────────────────────────────

def render_tree(node: Node, prefix: str, rich_node: RichTree | None = None) -> RichTree:
    style, glyph = STATUS_STYLE.get(node.status, ("white", "•"))
    label = Text()
    label.append(f"{glyph} ", style=style)
    # Always show the class/tag (bold); add the instance name when it says
    # something the tag does not — minus the agent prefix, which is in the header.
    label.append(node.tag, style="bold")
    short = strip_prefix(node.name, prefix)
    if short and short != node.tag:
        label.append(f" {short}", style="italic grey70")
    if node.status != "IDLE":
        label.append(f"  {node.status}", style=style)
        if node.dur_ms:
            label.append(f"  {node.dur_ms:>6.2f} ms", style="grey50")
        if node.ticks:
            label.append(f"  ×{node.ticks}", style="grey37")

    branch = RichTree(label, guide_style="grey37") if rich_node is None else rich_node.add(label)
    for child in node.children:
        render_tree(child, prefix, branch)
    return branch


def make_header(
    agent: str, endpoint: str, total: int, other: int, rate: float, last_ts: float
) -> Text:
    now = time.monotonic()
    if total == 0:
        conn = Text("○ waiting for events", style="grey50")
    elif now - last_ts > IDLE_THRESHOLD:
        conn = Text("◌ idle", style="yellow")
    else:
        conn = Text("● live", style="green")
    header = Text()
    header.append("BTEng Live Tree", style="bold white")
    header.append(f"   agent: {agent}", style="cyan")
    header.append(f"   {endpoint}   ", style="grey50")
    header.append_text(conn)
    header.append(f"   {rate:>4.0f} ev/s   {total} total", style="grey50")
    if other:
        header.append(f"   +{other} from other trees", style="grey37")
    return header


class TreeView:
    """Holds the skeleton + live state and turns ZMQ events into colored frames."""

    def __init__(self, root: Node, agent: str, prefix: str, endpoint: str) -> None:
        self.root = root
        self.agent = agent
        self.prefix = prefix
        self.endpoint = endpoint
        self.by_name: dict[str, Node] = {}
        self.by_tag: dict[str, Node] = {}
        build_lookup(root, self.by_name, self.by_tag)
        self.recent: deque[float] = deque()  # event timestamps in the last second
        self.total = 0    # events routed into THIS tree
        self.other = 0    # events belonging to another agent
        self.last_ts = 0.0

    def resolve(self, evt: dict) -> Node | None:
        """Map one event onto a node of this tree, or None if it is not ours.

        Deliberately keyed on the name and not on the event ``uid``: uids are
        per-tree counters, so caching them here would happily route Pinky's
        node #7 into Blinky's node #7.  Names carry the agent, uids do not.
        """
        name = str(evt.get("name", ""))
        if not name:
            return None
        head, sep, _tail = name.partition("/")
        if sep:
            if head.lower() != self.prefix.lower():
                self.other += 1  # another agent's tree — counted, not drawn
                return None
            node = self.by_name.get(name.lower())
            if node is None:
                # Ours by prefix but absent from the XML: the file on disk is out
                # of date. Graft it under the root so nothing goes missing.
                node = Node(str(evt.get("type", "node")), name)
                self.root.children.append(node)
                self.by_name[name.lower()] = node
            return node
        # Unprefixed name: cannot tell whose it is, so fall back to the class tag
        # and only accept it when this tree actually has such a node.
        return self.by_name.get(name.lower()) or self.by_tag.get(name)

    def apply(self, evt: dict) -> None:
        node = self.resolve(evt)
        if node is None:
            return
        node.status = str(evt.get("status", node.status))
        node.dur_ms = float(evt.get("dur_ms", 0.0) or 0.0)
        node.ticks += 1
        node.last_ts = time.monotonic()
        self.total += 1
        self.last_ts = node.last_ts
        self.recent.append(node.last_ts)

    def renderable(self) -> Group:
        now = time.monotonic()
        while self.recent and now - self.recent[0] > 1.0:
            self.recent.popleft()
        header = make_header(
            self.agent, self.endpoint, self.total, self.other, len(self.recent), self.last_ts
        )
        return Group(header, Text(), render_tree(self.root, self.prefix))


def resolve_xml(args: argparse.Namespace) -> Path:
    """Locate the XML to draw, or exit with something actionable."""
    xml_path = args.xml or BT_STRUCTURES / AGENT_TREES[args.agent]
    if xml_path.is_file():
        return xml_path
    available = sorted(p.name for p in BT_STRUCTURES.glob("*.xml"))
    sys.exit(
        f"Behavior-tree XML not found: {xml_path}\n"
        + (
            f"Available in {BT_STRUCTURES}: {', '.join(available)}"
            if available
            else f"{BT_STRUCTURES} contains no *.xml yet — nothing to draw."
        )
        + "\nPass an explicit file with --xml PATH."
    )


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    xml_path = resolve_xml(args)
    try:
        skeleton = load_skeleton(xml_path)
    except ET.ParseError as exc:
        sys.exit(f"{xml_path}: not valid XML — {exc}")

    prefix = detect_prefix(skeleton, args.agent.capitalize())
    endpoint = f"tcp://{args.host}:{args.port}"
    view = TreeView(skeleton, args.agent, prefix, endpoint)

    ctx = zmq.Context.instance()
    sock = ctx.socket(zmq.SUB)
    sock.connect(endpoint)
    sock.setsockopt(zmq.SUBSCRIBE, TOPIC)
    poller = zmq.Poller()
    poller.register(sock, zmq.POLLIN)

    with Live(view.renderable(), refresh_per_second=15, screen=False) as live:
        try:
            while True:
                if dict(poller.poll(timeout=100)).get(sock) == zmq.POLLIN:
                    _topic, _, payload = sock.recv().partition(b" ")
                    try:
                        evt = json.loads(payload)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(evt, dict):
                        view.apply(evt)
                live.update(view.renderable())
        except KeyboardInterrupt:
            pass
        finally:
            sock.close(linger=0)
            ctx.term()
    print("Stopped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
