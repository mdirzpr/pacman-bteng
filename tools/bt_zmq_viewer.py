#!/usr/bin/env python3
"""Flat live log of the BTEng ZmqPublisher tick stream.

``pacman_bteng/introspection/stream.py`` attaches a BTEng ``ZmqPublisher`` to the
executor, which streams every tick event as JSON over a ZMQ ``PUB`` socket
(topic ``bteng``) on ``tcp://*:2667``.  Everything runs on the host, so the port
is reachable directly — just run this while the game is up:

    ./.venv/bin/python tools/bt_zmq_viewer.py        # or: ./run.sh viewer

This is the runnable equivalent of the copy-paste subscriber in docs/USAGE.md
and needs nothing but pyzmq.

The one thing that differs from a single-tree demo: this game ticks FIVE trees
against one stream (blinky, pinky, inky, clyde and — with ``--auto`` — pacman),
so the log interleaves all of them.  Node instance names carry the agent as a
prefix (``Blinky/Chase``), which is what ``--filter`` keys off:

    ./run.sh viewer --filter blinky                  # one ghost only
    ./run.sh viewer --status RUNNING,SUCCESS         # skip the FAILURE noise
    ./run.sh viewer --filter pinky --status RUNNING  # both, ANDed

There is no Groot2 here — Groot2 speaks BehaviorTree.CPP's wire protocol, which
is incompatible with BTEng's event stream.  ``tools/bt_tree_viewer.py`` is the
visual alternative.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

try:
    import zmq
except ImportError:
    sys.exit(
        "pyzmq is not installed. Install it with:  pip install -r requirements.txt\n"
        "(The game still runs without it; the introspection stream just stays off.)"
    )

DEFAULT_HOST = "localhost"
DEFAULT_PORT = 2667
TOPIC = b"bteng"

#: The statuses BTEng can report — also the accepted values of ``--status``.
KNOWN_STATUSES = ("SUCCESS", "FAILURE", "RUNNING", "IDLE", "SKIPPED")

# Plain ANSI, no dependency.  Only used when stdout is a TTY (see _colourise).
ANSI = {
    "SUCCESS": "\033[0;32m",   # green
    "FAILURE": "\033[0;31m",   # red
    "RUNNING": "\033[1;33m",   # yellow
    "SKIPPED": "\033[0;36m",   # cyan
    "IDLE": "\033[0;90m",      # grey
}
ANSI_RESET = "\033[0m"
ANSI_DIM = "\033[0;90m"


class _HelpFormatter(
    argparse.ArgumentDefaultsHelpFormatter, argparse.RawDescriptionHelpFormatter
):
    """Show defaults in the option list, keep the epilog's line breaks."""


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Watch the BTEng tick/status event stream live.",
        formatter_class=_HelpFormatter,
        epilog=(
            "Examples:\n"
            "  %(prog)s\n"
            "  %(prog)s --filter blinky\n"
            "  %(prog)s --filter Inky/ --status RUNNING,SUCCESS\n"
            "  %(prog)s --raw | jq .\n"
        ),
    )
    parser.add_argument(
        "--host",
        default=DEFAULT_HOST,
        help="Host where the ZmqPublisher is reachable.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_PORT,
        help="TCP port of the ZmqPublisher stream.",
    )
    parser.add_argument(
        "--raw",
        action="store_true",
        help="Print each event as raw JSON instead of a formatted line.",
    )
    parser.add_argument(
        "--filter",
        metavar="SUBSTR",
        default=None,
        help=(
            "Only show events whose node name contains SUBSTR (case-insensitive). "
            "Node names are agent-prefixed, so 'blinky' watches one ghost."
        ),
    )
    parser.add_argument(
        "--status",
        metavar="S[,S...]",
        default=None,
        help=(
            "Only show these statuses, comma separated: "
            + ",".join(KNOWN_STATUSES)
            + "."
        ),
    )
    args = parser.parse_args(argv)

    # Normalise --status here rather than at every event: parse once, fail loud.
    if args.status:
        wanted = {s.strip().upper() for s in args.status.split(",") if s.strip()}
        unknown = sorted(wanted - set(KNOWN_STATUSES))
        if unknown:
            parser.error(
                f"unknown status {', '.join(unknown)} — pick from "
                + ", ".join(KNOWN_STATUSES)
            )
        args.statuses = wanted
    else:
        args.statuses = None

    args.needle = args.filter.lower() if args.filter else None
    return args


def keep(evt: dict, needle: str | None, statuses: set[str] | None) -> bool:
    """True when the event passes both filters (they are ANDed)."""
    if needle is not None and needle not in str(evt.get("name", "")).lower():
        return False
    if statuses is not None and str(evt.get("status", "")).upper() not in statuses:
        return False
    return True


def format_event(evt: dict, colour: bool) -> str:
    """One aligned line per event.

    The status word is padded *before* it is wrapped in escape codes — colouring
    first would make the invisible bytes count towards the column width and the
    whole log would go ragged.
    """
    name = str(evt.get("name", ""))
    status = str(evt.get("status", ""))
    reason = str(evt.get("reason", "") or "")
    duration = float(evt.get("dur_ms", 0.0) or 0.0)

    status_cell = f"{status:<8}"
    if colour:
        status_cell = f"{ANSI.get(status, '')}{status_cell}{ANSI_RESET}"
        reason = f"{ANSI_DIM}{reason}{ANSI_RESET}" if reason else reason

    # 28 wide: "Blinky/StepTowardTarget" and friends fit without wrapping.
    return f"{name:<28} {status_cell} {duration:>6.2f} ms {reason}".rstrip()


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    endpoint = f"tcp://{args.host}:{args.port}"
    colour = sys.stdout.isatty()

    ctx = zmq.Context.instance()
    sock = ctx.socket(zmq.SUB)
    sock.connect(endpoint)
    sock.setsockopt(zmq.SUBSCRIBE, TOPIC)  # topic prefix

    banner = f"Listening for BTEng tick events on {endpoint}"
    if args.needle:
        banner += f"   filter: {args.filter!r}"
    if args.statuses:
        banner += f"   status: {','.join(sorted(args.statuses))}"
    print(f"{banner} … (Ctrl-C to quit)")

    try:
        while True:
            raw = sock.recv()
            _topic, _, payload = raw.partition(b" ")  # "bteng <json>"
            try:
                evt = json.loads(payload)
            except json.JSONDecodeError:
                # Never silently drop something we cannot parse — a malformed
                # payload is a bug worth seeing.
                print(f"<unparseable> {payload!r}")
                continue
            if not isinstance(evt, dict) or not keep(evt, args.needle, args.statuses):
                continue
            if args.raw:
                # Filters still apply in --raw mode; only the rendering changes.
                print(payload.decode("utf-8", "replace"))
            else:
                print(format_event(evt, colour))
    except KeyboardInterrupt:
        print("\nStopped.")
    except BrokenPipeError:
        # Piped into head/less and the reader walked away — not an error. Point
        # stdout at /dev/null so the interpreter's own flush cannot re-raise.
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
    finally:
        sock.close(linger=0)
        ctx.term()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
