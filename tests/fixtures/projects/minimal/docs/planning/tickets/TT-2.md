---
title: Task list + detail REST endpoints
milestone: v1
track: backend
status: in-flight
owner: backend-guild
---

# TT-2 · Task REST endpoints

Expose the persisted tasks over a versioned read API so the frontend can render
lists and detail views without touching the database directly.

## Endpoints

- `GET /api/v1/tasks` — paginated list projection.
- `GET /api/v1/tasks/{id}` — single task with full body.

### Response shape

| Field      | Type     | Present in list | Present in detail |
|------------|----------|-----------------|-------------------|
| `id`       | `string` | yes             | yes               |
| `title`    | `string` | yes             | yes               |
| `state`    | `string` | yes             | yes               |
| `body`     | `string` | no              | yes               |

## Handler sketch

```python
@router.get("/tasks/{task_id}")
def get_task(task_id: str, svc: TaskService = Depends(task_service)) -> TaskDetail:
    task = svc.get(task_id)
    if task is None:
        raise TaskNotFound(task_id)
    return TaskDetail.from_domain(task)
```

Ordering follows the stored `position`[^ordering] so the board and the list
agree on sequence.

[^ordering]: Position is the single source of truth for order; the API never
    re-sorts by `created_at`, which would desync the drag-and-drop board.
