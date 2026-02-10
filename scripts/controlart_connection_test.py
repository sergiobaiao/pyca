#!/usr/bin/env python3
"""Simple connection test script for Controlart TCP transport.

Example:
  python scripts/controlart_connection_test.py --host 192.168.1.50 --port 5000
  python scripts/controlart_connection_test.py --host 192.168.1.50 --port 5000 \
      --command getmodulelist --command getmodulesstatus --duration 8
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path
import sys
import threading
import time
from typing import List

# Ensure repository root is importable when running this script directly.
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
  sys.path.insert(0, str(REPO_ROOT))

from controlart import TcpLineTransport, TransportConfig

_DEFAULT_COMMANDS = ["getmodulelist", "getmodulesstatus"]


def _build_parser() -> argparse.ArgumentParser:
  parser = argparse.ArgumentParser(description="Test Controlart TCP transport connectivity.")
  parser.add_argument("--host", required=True, help="XPORT host/IP.")
  parser.add_argument("--port", required=True, type=int, help="XPORT TCP port.")
  parser.add_argument(
      "--duration",
      type=float,
      default=10.0,
      help="How long (seconds) to keep the test running before shutdown.")
  parser.add_argument(
      "--command",
      action="append",
      default=[],
      help=("Command to send after connect. Can be repeated. "
            "Defaults to getmodulelist + getmodulesstatus when omitted."))
  parser.add_argument(
      "--connect-timeout",
      type=float,
      default=3.0,
      help="Socket connect timeout (seconds).")
  parser.add_argument(
      "--read-timeout",
      type=float,
      default=1.0,
      help="Socket read timeout (seconds).")
  return parser


def main() -> int:
  args = _build_parser().parse_args()
  logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

  received_lines: List[str] = []
  connected_event = threading.Event()

  def on_line(line: str) -> None:
    received_lines.append(line)
    print(f"RX: {line}")

  def on_state(state: str) -> None:
    print(f"STATE: {state}")
    if state == "connected":
      connected_event.set()

  config = TransportConfig(
      host=args.host,
      port=args.port,
      connect_timeout_s=args.connect_timeout,
      read_timeout_s=args.read_timeout)

  transport = TcpLineTransport(config, on_line, state_handler=on_state)
  start = time.time()

  try:
    transport.start()

    # Wait a short period for first successful connect state.
    connected = connected_event.wait(timeout=max(args.connect_timeout + 1.0, 2.0))
    commands = args.command or _DEFAULT_COMMANDS

    if connected:
      print("Connected successfully. Sending commands:")
      for cmd in commands:
        print(f"TX: {cmd}")
        transport.send_line(cmd)
    else:
      print("Did not observe a connected state within timeout.")

    deadline = start + max(args.duration, 0.0)
    while time.time() < deadline:
      time.sleep(0.1)

  finally:
    transport.stop()

  elapsed = time.time() - start
  print("--- Summary ---")
  print(f"Elapsed: {elapsed:.2f}s")
  print(f"Received lines: {len(received_lines)}")
  if received_lines:
    print("Last line:", received_lines[-1])

  return 0


if __name__ == "__main__":
  raise SystemExit(main())
