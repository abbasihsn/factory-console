---
title: Room membership + presence
milestone: MVP
track: backend
status: todo
---

# WS-2 · Room membership + presence

Let a connection join and leave rooms, and broadcast presence so members see
who is online.

## Frames

- `join` — subscribe the connection to a room.
- `leave` — unsubscribe.
- `presence` — periodic heartbeat, refreshed every 20s.

## Presence table

| State     | Meaning                              |
|-----------|--------------------------------------|
| `online`  | heartbeat within the last 20s        |
| `idle`    | no heartbeat for 20–60s              |
| `offline` | connection dropped or timed out      |

```python
async def join_room(conn: Conn, room_id: str) -> None:
    rooms.add(room_id, conn.id)
    await broadcast(room_id, {"type": "presence", "connId": conn.id, "state": "online"})
```

Heartbeats are coalesced[^coalesce] so a burst of frames collapses to one
broadcast per tick.

[^coalesce]: Without coalescing, a reconnect storm would N-square the presence
    broadcasts across a busy room.
