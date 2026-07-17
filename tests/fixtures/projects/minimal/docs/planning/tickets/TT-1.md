---
title: Bootstrap task store schema + migrations
milestone: MVP
track: data
status: merged
estimate: 3
---

# TT-1 · Bootstrap task store schema

Establish the durable persistence layer the rest of the tracker builds on. This
ticket lands the baseline `tasks` table plus a repeatable migration so every
environment converges on the same shape.

## Goals

- Define the canonical `tasks` schema (id, title, state, position, timestamps).
- Ship an idempotent baseline migration runnable on a fresh database.
- Seed a fixture dataset the API tests can read against.

## Schema

| Column       | Type        | Notes                                    |
|--------------|-------------|------------------------------------------|
| `id`         | `uuid`      | primary key, server-generated            |
| `title`      | `text`      | non-empty, trimmed                        |
| `state`      | `text`      | one of `todo`, `doing`, `done`[^states]  |
| `position`   | `integer`   | ordering within a column                  |
| `created_at` | `timestamp` | UTC, defaulted at insert                  |

## Baseline migration

```python
def upgrade() -> None:
    op.create_table(
        "tasks",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("state", sa.Text(), nullable=False, server_default="todo"),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
```

[^states]: The `state` column is intentionally a free text check-constrained
    field rather than a native enum so new columns can be added without a
    coordinated migration across services.
