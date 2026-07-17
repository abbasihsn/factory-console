---
title: Message fan-out + delivery receipts
milestone: v1
track: backend
status: in-flight
---

# WS-3 · Message fan-out + delivery receipts

Dispatch an inbound message to every member of a room and collect per-recipient
delivery receipts.

> Note: this ticket declares a dependency on `WS-404`, which is intentionally
> **absent** from the manifest, so the console surfaces it under
> `unresolvedDeps`.

## Delivery states

- `sent` — accepted by the gateway.
- `delivered` — pushed to the recipient socket.
- `read` — recipient acknowledged.

## Receipt shape

| Field        | Type      | Notes                       |
|--------------|-----------|-----------------------------|
| `messageId`  | `string`  | server-generated            |
| `recipient`  | `string`  | member conn id              |
| `state`      | `string`  | sent / delivered / read     |

```python
async def fan_out(room_id: str, message: Message) -> None:
    for conn in rooms.members(room_id):
        await conn.send_json(message.as_frame())
        receipts.mark(message.id, conn.id, "delivered")
```

Back-pressure is bounded per connection[^bp].

[^bp]: A slow consumer is dropped after its outbound queue exceeds the high-water
    mark, rather than stalling the whole room.
