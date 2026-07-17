# Roadmap — Task Tracker

A small, three-milestone tracker used as the `minimal` console fixture. It has
no factory run-state directory, so every ticket resolves to `unknown` run-state.

## MVP

Stand up the persistence spine.

- **TT-1** — Bootstrap task store schema + migrations.

## v1

Make the data reachable over HTTP.

- **TT-2** — Task list + detail REST endpoints (depends on TT-1).

## v2

Give the data a face.

- **TT-3** — Task board drag-and-drop UI (depends on TT-2).
