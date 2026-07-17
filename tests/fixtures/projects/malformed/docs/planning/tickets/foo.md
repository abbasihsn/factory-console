---
title: A perfectly reasonable ticket in a broken manifest
milestone: MVP
track: backend
status: todo
---

# foo · Otherwise-valid ticket body

The point of this fixture is that `docs/planning/tickets.json` is **invalid
JSON** (a trailing comma), so the manifest parser must raise
`MalformedManifest`. This `.md` file, by contrast, is perfectly well-formed —
proving the console fails at manifest parse time, not because the ticket body is
broken.

## Notes

- The body renders fine on its own.
- Only the manifest is corrupt.

```python
# The manifest above trips json.loads with a JSONDecodeError.
json.loads(Path("docs/planning/tickets.json").read_text())
```
