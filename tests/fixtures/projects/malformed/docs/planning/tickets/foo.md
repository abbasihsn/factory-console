---
id: foo
title: Otherwise reasonable ticket in a broken manifest
status: planned
track: backend
milestone: MVP
dependsOn: []
provides:
  - foo.thing
---

# Otherwise reasonable ticket in a broken manifest

This ticket file is structurally valid on its own — well-formed front-matter and a
real body. Only the sibling `tickets.json` is broken (a trailing comma), so tests
can assert that the parser fails on the manifest while the per-ticket `.md` still
parses cleanly.

## Purpose

- Exercise the manifest error path (exit code 3, "malformed manifest").
- Prove a good `.md` next to a bad manifest is not itself the cause of failure.

## Cases

| Artifact            | Valid? |
| ------------------- | ------ |
| `tickets.json`      | no     |
| `tickets/foo.md`    | yes    |

## Reference

```python
import json, pathlib

json.loads(pathlib.Path("docs/planning/tickets.json").read_text())  # raises
```

The failure must surface as a clean, typed error rather than a traceback[^exit].

[^exit]: The CLI contract maps a malformed manifest to exit code 3; this fixture is
    the input that should trigger exactly that path.
