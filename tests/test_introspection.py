"""The introspection stream — and the one way it used to hang the game.

Everything here is about start-up and shutdown rather than about events: the
Inspector itself is BTEng's, but *how* we attach a publisher to it is ours, and
getting it wrong wedged the process badly enough that SIGTERM could not end it.
"""

from __future__ import annotations

import socket

import pytest

from pacman_bteng.config import IntrospectionSettings
from pacman_bteng.introspection.stream import BTIntrospection, _port_is_free


@pytest.fixture
def taken_port():
    """A TCP port held open for the duration of one test."""
    holder = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    holder.bind(("", 0))
    holder.listen(1)
    yield holder.getsockname()[1]
    holder.close()


def test_disabled_stream_starts_nothing() -> None:
    intro = BTIntrospection(IntrospectionSettings(enable_zmq=False))
    try:
        assert intro.endpoint is None
        assert intro.warnings == []
        assert intro.inspector is not None
    finally:
        intro.close()


def test_a_free_port_gets_a_publisher() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("", 0))
        port = probe.getsockname()[1]

    intro = BTIntrospection(IntrospectionSettings(enable_zmq=True, zmq_port=port))
    try:
        assert intro.endpoint == f"tcp://*:{port}"
        assert intro.warnings == []
    finally:
        intro.close()
    assert intro.endpoint is None


def test_a_busy_port_degrades_instead_of_deadlocking(taken_port: int) -> None:
    # THE REGRESSION. BTEng's ZmqPublisher._run() binds outside its try/finally,
    # so a clash leaks an unclosed socket into the global ZMQ context and
    # pyzmq's atexit ctx.term() then blocks forever — the process ignores both
    # Ctrl-C and SIGTERM and needs SIGKILL. Running the game twice was enough to
    # trigger it, so the port is probed before the publisher is ever built.
    intro = BTIntrospection(IntrospectionSettings(enable_zmq=True, zmq_port=taken_port))
    try:
        assert intro.endpoint is None, "must not claim a stream it does not have"
        assert any("already in use" in w for w in intro.warnings)
        # The game still has to be playable, and the trees still observable.
        assert intro.inspector is not None
    finally:
        intro.close()


def test_port_probe_agrees_with_reality(taken_port: int) -> None:
    assert _port_is_free(taken_port) is False
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("", 0))
        free = probe.getsockname()[1]
    assert _port_is_free(free) is True


def test_close_is_idempotent() -> None:
    intro = BTIntrospection(IntrospectionSettings(enable_zmq=False))
    intro.close()
    intro.close()          # a second close must not raise on the way out
