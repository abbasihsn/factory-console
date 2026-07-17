---
title: Websocket gateway skeleton
milestone: MVP
track: backend
status: todo
---

# WS-1 · Websocket gateway skeleton

Stand up the long-lived websocket endpoint and a registry that maps a live
connection to an authenticated session.

## Scope

- Accept `ws://.../v1/stream` upgrades.
- Register/deregister connections in an in-memory registry.
- Emit a `hello` frame with the negotiated protocol version.

## Registry entry

| Field         | Type      | Notes                          |
|---------------|-----------|--------------------------------|
| `connId`      | `string`  | server-generated, opaque       |
| `sessionId`   | `string`  | filled by WS-5 handshake       |
| `connectedAt` | `iso8601` | UTC connect timestamp          |

```python
async def on_connect(ws: WebSocket) -> None:
    conn = registry.add(ws)
    await ws.send_json({"type": "hello", "protocol": 1, "connId": conn.id})
```

The registry is deliberately in-memory[^scale] for the MVP.

[^scale]: A shared registry (Redis) is deferred to v2 once multi-process
    fan-out is required.
