---
title: Auth handshake on connect
milestone: MVP
track: backend
status: merged
---

# WS-5 · Auth handshake on connect

Authenticate a websocket at connect time by validating a short-lived token
before the connection is allowed to join any room.

## Handshake

1. Client opens the socket with a `token` query parameter.
2. Gateway validates the token signature and expiry.
3. On success the registry entry is stamped with the resolved `sessionId`.
4. On failure the socket is closed with code `4401`.

## Close codes

| Code   | Meaning                       |
|--------|-------------------------------|
| `1000` | normal closure                |
| `4401` | unauthenticated / bad token   |
| `4403` | authenticated but forbidden   |

```python
async def authenticate(ws: WebSocket, token: str) -> Session | None:
    claims = verify_token(token)
    if claims is None:
        await ws.close(code=4401)
        return None
    return Session(id=claims["sub"])
```

Tokens are never logged[^tok].

[^tok]: Only the resolved `sessionId` is logged; the raw bearer token is treated
    as a secret and redacted at the boundary.
