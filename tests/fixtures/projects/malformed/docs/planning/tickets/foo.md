---
id: foo
title: Seed a single well-formed ticket
status: todo
track: ingestion
milestone: MVP
dependsOn: []
provides: One valid ticket whose manifest entry is unreadable due to malformed JSON
files:
  - server/trailmark/ingest/csv_dropbox.py
---

# Seed a single well-formed ticket

This ticket file is deliberately valid. The `malformed` fixture exists to prove
that the parser fails loudly on a broken `tickets.json` — but the per-ticket
markdown alongside it is perfectly well-formed, so tests can assert that the
failure is isolated to manifest parsing and not the `.md` reader.

## Why this shape

- The sibling `docs/planning/tickets.json` carries a trailing comma, so
  `json.loads` must raise `json.JSONDecodeError`.
- This body still exercises the normal markdown surface: headings, a list, a
  table, a footnote[^scope], and a fenced block.

### Expected failure

```python
import json, pathlib
raw = pathlib.Path("docs/planning/tickets.json").read_text()
json.loads(raw)  # raises json.JSONDecodeError — that is the point
```

| Concern           | This file | Sibling manifest |
|-------------------|-----------|------------------|
| Front-matter      | valid     | n/a              |
| JSON well-formed  | n/a       | broken (comma)   |
| Parser should     | succeed   | raise            |

[^scope]: Keeping the markdown valid isolates the malformed-JSON case so a
failing manifest test can't be blamed on the `.md` reader.
