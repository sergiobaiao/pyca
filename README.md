pylutron + controlart (work in progress)
=====

This repository now contains:

- **`pylutron`**: the original Python library for controlling a Lutron RadioRA 2 system.
- **`controlart`**: a new, in-progress Python library targeting Controlart XPORT/XBUS devices.

## pylutron (existing)
A simple Python library for controlling a Lutron RadioRA 2 system with a Main Repeater.

### Installation
You can get the code from `https://github.com/thecynic/pylutron`

### Example
```python
import pylutron

rra2 = pylutron.Lutron("192.168.0.x", "lutron", "integration")
rra2.load_xml_db()
rra2.connect()
```

## controlart (new)
The new `controlart` package currently includes **implementation step 1**:

- reconnecting TCP transport,
- line-oriented receive loop,
- normalized `\r\n` command framing,
- bounded reconnect backoff,
- testable socket factory injection.

### Current API surface
```python
from controlart import TcpLineTransport, TransportConfig
```

### Minimal transport example
```python
from controlart import TcpLineTransport, TransportConfig


def on_line(line: str) -> None:
    print("RX:", line)


cfg = TransportConfig(host="192.168.1.50", port=5000)
transport = TcpLineTransport(cfg, on_line)
transport.start()
transport.send_line("getmodulelist")

# ...later
transport.stop()
```

### Status
`controlart` is under active development; higher-level codec/router/entity APIs are planned next.

License
-------
This code is released under the MIT license.
