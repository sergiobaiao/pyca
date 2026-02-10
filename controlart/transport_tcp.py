"""TCP line transport with reconnect support for Controlart XPORT endpoints."""

from __future__ import annotations

from dataclasses import dataclass
import logging
import socket
import threading
import time
from typing import Callable, Optional

_LOGGER = logging.getLogger(__name__)

LineHandler = Callable[[str], None]
StateHandler = Callable[[str], None]
SocketFactory = Callable[[], socket.socket]


@dataclass(frozen=True)
class TransportConfig:
  """Settings for :class:`TcpLineTransport`."""

  host: str
  port: int
  connect_timeout_s: float = 3.0
  read_timeout_s: float = 1.0
  reconnect_min_delay_s: float = 0.25
  reconnect_max_delay_s: float = 5.0


class TcpLineTransport:
  """Background TCP transport that emits CRLF-delimited lines.

  Responsibilities:
  - Maintain a persistent TCP connection to host/port.
  - Reconnect with bounded backoff after failures.
  - Send outgoing lines terminated by CRLF (\r\n).
  - Invoke callback handlers for inbound lines and state transitions.
  """

  def __init__(
      self,
      config: TransportConfig,
      line_handler: LineHandler,
      *,
      state_handler: Optional[StateHandler] = None,
      socket_factory: Optional[SocketFactory] = None):
    self._config = config
    self._line_handler = line_handler
    self._state_handler = state_handler
    self._socket_factory = socket_factory

    self._thread: Optional[threading.Thread] = None
    self._lock = threading.Lock()
    self._sock: Optional[socket.socket] = None
    self._running = False
    self._send_cv = threading.Condition(self._lock)
    self._send_queue = []

  def start(self) -> None:
    """Start background I/O thread."""
    with self._lock:
      if self._running:
        return
      self._running = True
      self._thread = threading.Thread(target=self._run, name="controlart-transport", daemon=True)
      self._thread.start()

  def stop(self) -> None:
    """Stop background thread and close socket."""
    with self._lock:
      if not self._running:
        return
      self._running = False
      self._send_cv.notify_all()
      self._close_locked()
      thread = self._thread

    if thread is not None:
      thread.join(timeout=2.0)

  def send_line(self, line: str) -> None:
    """Queue a line to be sent; line ending is normalized to CRLF."""
    normalized = line.rstrip("\r\n") + "\r\n"
    payload = normalized.encode("utf-8")
    with self._lock:
      self._send_queue.append(payload)
      self._send_cv.notify_all()

  def _notify_state(self, state: str) -> None:
    if self._state_handler is not None:
      self._state_handler(state)

  def _new_socket(self) -> socket.socket:
    if self._socket_factory is not None:
      return self._socket_factory()
    return socket.create_connection(
        (self._config.host, self._config.port),
        timeout=self._config.connect_timeout_s)

  def _connect(self) -> None:
    sock = self._new_socket()
    sock.settimeout(self._config.read_timeout_s)
    with self._lock:
      self._sock = sock
    self._notify_state("connected")

  def _close_locked(self) -> None:
    sock = self._sock
    self._sock = None
    if sock is not None:
      try:
        sock.close()
      except OSError:
        pass

  def _flush_send_queue(self) -> None:
    while True:
      with self._lock:
        if self._sock is None or not self._send_queue:
          return
        payload = self._send_queue.pop(0)
        sock = self._sock
      assert sock is not None
      sock.sendall(payload)

  def _read_lines(self) -> None:
    with self._lock:
      sock = self._sock
    if sock is None:
      return

    buf = b""
    while self._is_running():
      self._flush_send_queue()
      try:
        chunk = sock.recv(4096)
      except socket.timeout:
        continue
      if not chunk:
        raise EOFError("peer closed")
      buf += chunk
      while b"\n" in buf:
        raw, buf = buf.split(b"\n", 1)
        line = raw.rstrip(b"\r").decode("utf-8", errors="replace")
        self._line_handler(line)

  def _is_running(self) -> bool:
    with self._lock:
      return self._running

  def _run(self) -> None:
    delay = self._config.reconnect_min_delay_s
    while self._is_running():
      try:
        self._notify_state("connecting")
        self._connect()
        delay = self._config.reconnect_min_delay_s
        self._read_lines()
      except Exception as exc:  # broad on purpose: keep reconnecting
        _LOGGER.debug("transport loop exception: %s", exc)
      finally:
        with self._lock:
          self._close_locked()
        self._notify_state("disconnected")

      if not self._is_running():
        break

      time.sleep(delay)
      delay = min(delay * 2.0, self._config.reconnect_max_delay_s)
