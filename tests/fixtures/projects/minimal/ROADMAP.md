# Pocket Ledger — Roadmap

A single-file, offline-first personal ledger driven from the command line.

## MVP — a durable store

The ledger is trustworthy before it is convenient: transactions persist to a local
SQLite file, survive restarts, and back up with a plain file copy.

- **T01 — Persist ledger entries in a local SQLite store** · data · done

## v1 — make sense of the numbers

Turn stored entries into something a person reads at month end.

- **T07 — Monthly rollup report command** · cli · planned

## v2 — automation

Reduce manual entry for the transactions that repeat every month.

- **T12 — Recurring transaction scheduler** · backend · planned
