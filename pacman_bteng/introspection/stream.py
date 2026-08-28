"""BTIntrospection — one Inspector for all five trees, fanned out to sinks.

Why one shared Inspector rather than one per brain: the viewers subscribe to a
single ZMQ endpoint, and a ghost's decision only makes sense next to what the
other three were doing at that instant.  Node instance names are prefixed with
the agent (``Blinky/Chase``), which is what keeps a single stream readable and
what ``tools/bt_zmq_viewer.py --filter blinky`` keys off.

The Logger is wired here by hand instead of through ``TreeExecutor.set_logger``:
that method subscribes the logger to the inspector *per executor*, so with five
executors sharing one inspector every transition would be printed five times.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, List, Optional

from bteng import Inspector, Logger, LogLevel

if TYPE_CHECKING:  # pragma: no cover - typing only
    from pacman_bteng.config import IntrospectionSettings


class BTIntrospection:
    """Owns the Inspector, the Logger and the ZmqPublisher for one run."""

    def __init__(self, settings: "IntrospectionSettings") -> None:
        self.settings = settings
        self.inspector = Inspector.create()
        self.logger: Optional[Logger] = None
        self.endpoint: Optional[str] = None
        self.warnings: List[str] = []

        self._publisher: Any = None
        self._logger_sub_id: Optional[int] = None

        self._start_logger()
        self._start_publisher()

    # ── Sinks ─────────────────────────────────────────────────────────────────

    def _start_logger(self) -> None:
        if not (self.settings.console_log or self.settings.log_file):
            return

        self.logger = Logger.create()
        if self.settings.console_log:
            self.logger.add_console_sink(colored=True)
        if self.settings.log_file:
            try:
                self.logger.add_json_file_sink(self.settings.log_file)
            except OSError as exc:
                self.warnings.append(f"log file {self.settings.log_file}: {exc}")

        logger = self.logger

        def relay(record: Any) -> None:
            logger.log_transition(
                record.uid,
                record.name,
                record.old_status,
                record.status,
                record.duration,
                record.feedback_message,
            )

        self._logger_sub_id = self.inspector.subscribe(relay)

    def _start_publisher(self) -> None:
        if not self.settings.enable_zmq:
            return
        try:
            from bteng import ZmqPublisher
        except ImportError as exc:  # pragma: no cover - bteng[zmq] is declared
            self.warnings.append(f"ZMQ stream disabled: {exc}")
            return

        port = self.settings.zmq_port
        if not _port_is_free(port):
            # MUST be checked before constructing the publisher.  BTEng's
            # ZmqPublisher._run() calls sock.bind() outside its try/finally, so a
            # port clash leaks an unclosed socket into the global ZMQ context,
            # and pyzmq's atexit ctx.term() then blocks forever waiting for it.
            # The process hangs on exit and answers neither Ctrl-C nor SIGTERM;
            # only SIGKILL ends it.  Starting the game twice is all it takes, so
            # this is look-before-you-leap, not a defensive nicety.
            self.warnings.append(
                f"ZMQ stream disabled: port {port} is already in use "
                f"(another game running?). Pass --zmq-port N, or --no-zmq."
            )
            return

        try:
            publisher = ZmqPublisher(port=port)
        except ImportError as exc:
            # pyzmq missing: the game is perfectly playable without it, so this
            # is a warning on the HUD, never a failure to start.
            self.warnings.append(f"ZMQ stream disabled: {exc}")
            return

        publisher.attach(self.inspector).start()
        self._publisher = publisher
        self.endpoint = f"tcp://*:{port}"

    # ── Wiring ────────────────────────────────────────────────────────────────

    def attach(self, executor: Any) -> None:
        """Point a ``TreeExecutor`` at this Inspector.

        :class:`~pacman_bteng.behaviors.AgentBrain` already does this at
        construction; the method exists for trees built outside a brain (tests,
        experiments in a REPL).
        """
        executor.set_inspector(self.inspector)

    # ── Reporting ─────────────────────────────────────────────────────────────

    def summary(self) -> str:
        """One line for the console banner at start-up."""
        parts = []
        parts.append(f"ZMQ {self.endpoint}" if self.endpoint else "ZMQ off")
        if self.settings.console_log:
            parts.append("console log on")
        if self.settings.log_file:
            parts.append(f"log file {self.settings.log_file}")
        return ", ".join(parts)

    def close(self) -> None:
        """Stop the publisher thread and release the log file descriptors."""
        if self._publisher is not None:
            self._publisher.stop()
            self._publisher = None
        if self._logger_sub_id is not None:
            self.inspector.unsubscribe(self._logger_sub_id)
            self._logger_sub_id = None
        if self.logger is not None:
            self.logger.close()
            self.logger = None
        self.endpoint = None


def _port_is_free(port: int) -> bool:
    """Can a TCP listener still bind *port*?

    Deliberately without SO_REUSEADDR: the question is whether ZMQ's own bind
    will succeed, and ZMQ does not set it either.  The probe socket is closed
    immediately, which leaves a brief window for something else to take the
    port — losing that race costs the event stream, not the game.
    """
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind(("", port))
        except OSError:
            return False
    return True
