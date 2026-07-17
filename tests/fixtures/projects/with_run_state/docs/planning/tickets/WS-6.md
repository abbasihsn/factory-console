---
title: Typing indicators
milestone: v2
track: frontend
status: todo
---

# WS-6 · Typing indicators

Show a lightweight "user is typing" hint in a room, debounced so it does not
flood the fan-out path.

> This ticket is present in the manifest but has **no run-state marker** on
> disk, so it resolves to `todo` under the "present dir but missing marker"
> rule.

## Behaviour

- Emit `typing:start` on first keystroke.
- Emit `typing:stop` after 3s of inactivity or on send.
- Collapse multiple typers into "several people are typing".

## States

| Typers | Rendered text                    |
|--------|----------------------------------|
| 1      | `Alice is typing…`               |
| 2      | `Alice and Bob are typing…`      |
| 3+     | `Several people are typing…`     |

```typescript
const stop = debounce(() => socket.send({ type: "typing:stop" }), 3000);
function onKey() {
  socket.send({ type: "typing:start" });
  stop();
}
```

The indicator is best-effort[^besteffort] and never blocks message send.

[^besteffort]: Dropped typing frames are harmless; only message frames carry
    delivery guarantees.
