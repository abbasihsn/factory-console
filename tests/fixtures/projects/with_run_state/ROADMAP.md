# Roadmap — Realtime Chat

The `with_run_state` console fixture: a six-ticket chat gateway that carries a
`.factory/run-state/` directory exercising every `RunState` value.

## MVP

The connection spine.

- **WS-1** — Websocket gateway skeleton. *(run-state: todo)*
- **WS-2** — Room membership + presence. *(run-state: todo)*
- **WS-5** — Auth handshake on connect. *(run-state: merged)*

## v1

Real message flow.

- **WS-3** — Message fan-out + delivery receipts. *(run-state: in-flight; depends on the unresolved `WS-404`)*
- **WS-4** — Chat message rendering + sanitization. *(run-state: ready)*

## v2

Presence polish.

- **WS-6** — Typing indicators. *(present in manifest, no marker → resolves to todo)*
