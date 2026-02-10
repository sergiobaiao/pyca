import socket
import threading
import unittest

from controlart.transport_tcp import TcpLineTransport, TransportConfig


class _FakeSocket:
  def __init__(self, chunks):
    self._chunks = list(chunks)
    self.sent = []
    self.closed = False
    self.timeout = None

  def settimeout(self, value):
    self.timeout = value

  def sendall(self, payload):
    self.sent.append(payload)

  def recv(self, _size):
    if not self._chunks:
      return b""
    item = self._chunks.pop(0)
    if isinstance(item, Exception):
      raise item
    return item

  def close(self):
    self.closed = True


class TransportTests(unittest.TestCase):
  def test_send_line_appends_crlf(self):
    fake = _FakeSocket([socket.timeout(), b""])
    created = []

    def factory():
      created.append(fake)
      return fake

    got_line = threading.Event()
    tr = TcpLineTransport(
        TransportConfig("127.0.0.1", 1, reconnect_max_delay_s=0.01),
        lambda _line: got_line.set(),
        socket_factory=factory)

    tr.start()
    tr.send_line("mdcmd_getmd,1,2,3")
    self.assertTrue(got_line.wait(0.2) or True)  # no line expected; just allow loop tick
    tr.stop()

    self.assertIn(b"mdcmd_getmd,1,2,3\r\n", fake.sent)

  def test_receives_and_splits_lines(self):
    fake = _FakeSocket([b"a,b\r\n", b"c,d\n", b""])
    lines = []

    tr = TcpLineTransport(
        TransportConfig("127.0.0.1", 1, reconnect_max_delay_s=0.01),
        lines.append,
        socket_factory=lambda: fake)
    tr.start()

    for _ in range(20):
      if len(lines) >= 2:
        break
      threading.Event().wait(0.01)

    tr.stop()
    self.assertEqual(lines[:2], ["a,b", "c,d"])

  def test_reconnect_uses_multiple_socket_instances(self):
    sockets = [_FakeSocket([b"x\n", b""]), _FakeSocket([b"y\n", b""])]
    idx = {"n": 0}
    lines = []

    def factory():
      n = idx["n"]
      idx["n"] += 1
      return sockets[min(n, len(sockets) - 1)]

    tr = TcpLineTransport(
        TransportConfig("127.0.0.1", 1, reconnect_min_delay_s=0.001, reconnect_max_delay_s=0.005),
        lines.append,
        socket_factory=factory)
    tr.start()

    for _ in range(80):
      if idx["n"] >= 2 and len(lines) >= 2:
        break
      threading.Event().wait(0.005)

    tr.stop()
    self.assertGreaterEqual(idx["n"], 2)
    self.assertIn("x", lines)
    self.assertIn("y", lines)


if __name__ == "__main__":
  unittest.main()
