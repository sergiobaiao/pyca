# Developer Blueprint for a ControlArt Python Library

This blueprint reverse-engineers **engineering techniques** from `pylutron` and adapts them to the Controlart XPORT PRO/XBUS protocol defined in `API - XPORT PRO & XBUS R03J.pdf`.

## 1. Architectural Overview & Design Patterns

### Core patterns to preserve
- **Facade / Gateway**: one top-level API client (`ControllerClient`) that owns transport, command encoding, registry routing, and topology/cache access.
- **Connection Worker + Reactor loop**: dedicated background worker maintains TCP session, reads line-delimited frames, and dispatches callbacks.
- **Observer/Event Bus**: entity-level subscriptions with strongly-typed events (`LEVEL_CHANGED`, `INPUT_CHANGED`, `MOTOR_MOVED`, etc.).
- **Parser/Decoder strategy**: split into command encoder and response decoder; decoder maps response families (`setmd`, `setdmmd`, `setbmmd`, `setkp*`) to typed state updates.
- **Registry Router**: route by `(response_family, module_mac3_5)` to target object(s).
- **Request Coalescing**: deduplicate in-flight read queries (`getmd`, `fgetmd`, etc.) to avoid command storms.

### Connection lifecycle (Controlart-adapted)
1. **Open TCP socket** to XPORT IP/port from config (the protocol is plain TCP client -> XPORT TCP server).
2. **Line protocol setup**: all outgoing commands must end with `\r\n` (`<CR><LF>`).
3. **Operational loop**:
   - write user commands,
   - continuously read responses/events,
   - decode and route into entity caches,
   - emit observer events.
4. **Reconnection policy**:
   - detect disconnect/read timeout,
   - reconnect with backoff,
   - re-sync state after reconnect (`getmodulelist` + `getmodulesstatus` baseline).
5. **Health checks / diagnostics**:
   - periodic `getnetworkstatus` for connectivity diagnostics,
   - parse textual network reports (DNS/P2P/MQTT lines) as gateway health telemetry.

### Event loop strategy (async events vs user commands)
- **Inbound (async push)**: TCP reader decodes each line; response family decides which entity parser executes.
- **Outbound (user command)**: API call builds command string and sends immediately with `\r\n` terminator.
- **Sync read API over async stream**:
  - fire read command,
  - wait on event/future tied to correlation heuristic (module + expected response family),
  - return cached normalized state.

> Practical implication: even if Controlart frames do not include explicit request IDs, you can still build robust waiters by matching `(MAC, response family, freshness window)`.

---

## 2. Core Class Hierarchy (Abstracted)

```text
GenericControllerFacade
├── GenericTcpTransport
├── GenericCommandCodec
├── GenericResponseRouter
├── GenericTopologyStore
│   ├── GenericAreaContainer (optional logical grouping)
│   └── GenericModuleEntity (base for xBus modules)
│       ├── GenericRelayModuleEntity
│       ├── GenericDimmerModuleEntity
│       ├── GenericMotorModuleEntity
│       ├── GenericKeypad3x3Entity
│       └── GenericKeypad6x3Entity
└── GenericGatewayHealthEntity

GenericEntityBase
└── (all concrete entities)

RequestMultiplexer
```

### Responsibilities
- **GenericControllerFacade**: public API, reconnect orchestration, startup sync, module registry.
- **GenericTcpTransport**: socket connect/read/write, reconnect signals, line framing.
- **GenericCommandCodec**:
  - validates args,
  - supports decimal and hex-address notation,
  - always appends `\r\n`.
- **GenericResponseRouter**:
  - identifies prefixes like `modulelist`, `sync_counter`, `setmd`, `setdmmd`, `setbmmd`, `setkp3x3md`, `setkp6x3md`,
  - dispatches to parser handlers.
- **GenericModuleEntity**: shared identity (MAC bytes/type), subscription hooks, last-seen timestamp.
- **Relay/Dimmer/Motor/Keypad entities**: decode family-specific payload shapes to normalized state.
- **GenericGatewayHealthEntity**: models `getnetworkstatus` diagnostics.
- **RequestMultiplexer**: one query, many waiters.

---

## 3. Data Flow & State Management

### A. Ingestion pipeline
1. TCP line received.
2. Normalize whitespace/newline and split by comma.
3. Identify **frame family** from first token.
4. Extract module key (`MAC3-MAC4-MAC5`) when present.
5. Decode payload to typed state object.
6. Update cache atomically.
7. Wake any request waiters.
8. Emit semantic event.

### B. Controlart command/response grammar (extracted)

#### 1) Transport framing
- Command strings terminate with `<CR><LF>` (`\r\n`).
- Address bytes may be decimal or hexadecimal (`$XX`, `0xXX`), and some terminal tools require escaping (`$$XX`).

#### 2) Gateway/global commands
- `mdcmd_setmasteronmd` → textual feedback `MasterOn`.
- `mdcmd_setmasteroffmd` → textual feedback `MasterOff`.
- `reset_xport_xport` → `OK` (followed by reconnect requirement).
- `getnetworkstatus` → multi-line textual diagnostics (DNS/P2P/TCP/MQTT/clock).
- `getmodulelist` → `modulelist,<MAC3-MAC4-MAC5-TYPE>,...`.
- `getmodulesstatus` → `sync_counter,n` + sequence of module state lines.

#### 3) Module state response families (important for parser design)
- `setmd,...` (relay/dimmer-like compact module states in several sections).
- `setdmmd,MAC,IN0..IN11,OUT0..OUT8` (12 inputs + 9 outputs).
- `setbmmd,MAC,IN0,IN1,OUT0,OUT1,POS` (motor status family).
- `setkp3x3md,...` / `setkp6x3md,...` (keypad input/sensor/output states).

### C. Abstract parser logic flow

#### Facade/router parser
```text
line -> tokens
family = tokens[0]
if family == "modulelist": decode topology
elif family == "sync_counter": track snapshot size
elif family startswith "set":
    mac = tokens[1]
    entity = registry.lookup(mac, family)
    entity.apply(tokens[2:])
    notify_waiters(mac, family)
    publish_event(entity)
else:
    route to gateway diagnostics/unknown handler
```

#### Motor (`setbmmd`) parser pattern
```text
expect: setbmmd,MAC,IN0,IN1,OUT0,OUT1,POS
validate length
parse ints
state = {
  in_a, in_b,
  out_a, out_b,
  position_0_255
}
update motor cache and movement/position event
```

#### Dense IO (`setdmmd`) parser pattern
```text
expect many fields: IN0..IN11 + OUT0..OUT8
coerce inputs to bool (0/1)
coerce outputs to int brightness/level
emit per-channel or batched STATE_CHANGED event
```

### D. State-management rules for the new library
- **Keep normalized cache + raw frame** for every entity.
- **Monotonic last_update timestamp** per module.
- **Event-after-cache update** ordering.
- **Unknown/partial frame tolerance** with structured warnings, not crashes.
- **Startup baseline sync** (`getmodulelist`, `getmodulesstatus`) before exposing “ready”.

---

## 4. Porting Blueprint (Controlart-specific Recipe)

### Recommended Python package layout
```text
controlart/
  __init__.py
  client.py               # public facade
  transport_tcp.py        # reconnecting TCP line transport
  codec.py                # command builder + token helpers
  router.py               # response family dispatch
  models.py               # normalized state dataclasses
  events.py               # event enums / payload types
  parser/
    gateway.py            # modulelist, networkstatus, sync_counter
    relay.py              # setmd
    dimmer.py             # setdmmd
    motor.py              # setbmmd
    keypad.py             # setkp3x3md, setkp6x3md
  entities/
    base.py
    module.py
    motor.py
    keypad.py
  errors.py
```

### Migration mapping from pylutron concepts
- `LutronConnection` ➜ `ControlartTcpTransport`.
- `Lutron.send(op, cmd, id, ...)` ➜ `codec.build("mdcmd_*", ...)` + `transport.write_line()`.
- `_ids[cmd_type][integration_id]` ➜ `registry[(family, mac3_5)]`.
- `handle_update(args)` polymorphism stays, but keyed by response family.
- `_RequestHelper` pattern reused for `getmd/fgetmd` query dedupe.

### Implementation order (pragmatic)
1. Build transport + reconnect + line framing.
2. Implement codec for core commands (`getmodulelist`, `getmodulesstatus`, `mdcmd_getmd`, `mdcmd_msendmd`, `mdcmd_togglemd`, `mdcmd_sendmd`).
3. Implement router + parser for `modulelist`, `sync_counter`, `setmd`, `setdmmd`, `setbmmd`.
4. Implement entity cache/events and observer API.
5. Add keypad families and gateway health parser.
6. Add integration tests with recorded transcript fixtures from Hercules/xConfig sessions.

### Test strategy (must-have)
- **Golden transcript tests**: text frames -> expected normalized states/events.
- **Reconnect tests**: socket drop + resync sequence.
- **Input fuzz tests**: malformed commas, missing fields, non-int tokens.
- **Concurrency tests**: simultaneous reads sharing one in-flight command.

---

## Quick Engineering Notes from the PDF that matter to your API design
- The protocol is fundamentally **line-based CSV over TCP** with strict `\r\n` termination.
- Module addressing uses only the **last 3 MAC bytes** in most command payloads.
- Response families are heterogeneous and should be treated as **separate schema contracts**.
- Network/gateway diagnostics are partly **human-text lines**, so keep a tolerant parser path for non-CSV operational feedback.
