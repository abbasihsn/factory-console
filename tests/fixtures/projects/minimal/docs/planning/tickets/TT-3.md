---
title: Task board drag-and-drop UI
milestone: v2
track: frontend
status: todo
---

# TT-3 · Task board UI

Render tasks as a Kanban board with drag-and-drop between columns, persisting
new positions back through the task API.

## Columns

The board mirrors the three task states:

- **Todo** — freshly created work.
- **Doing** — in progress, one owner.
- **Done** — merged / shipped.

## Interaction contract

| Gesture            | Effect                                        |
|--------------------|-----------------------------------------------|
| drag within column | reorder, PATCH new `position`                 |
| drag across column | change `state` + `position` in one request    |
| keyboard move      | same as drag, accessible fallback             |

## Optimistic update

```typescript
async function moveCard(card: Card, to: Column, index: number) {
  applyLocally(card, to, index);            // optimistic
  try {
    await api.patchTask(card.id, { state: to.state, position: index });
  } catch (err) {
    rollback(card);                          // revert on failure
    throw err;
  }
}
```

Positions are recomputed as sparse integers[^sparse] to avoid rewriting every
row on each move.

[^sparse]: New positions are inserted as the midpoint between neighbours; a
    periodic compaction job renormalises the gaps.
