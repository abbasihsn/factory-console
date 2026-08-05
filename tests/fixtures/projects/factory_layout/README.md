# fixture: factory_layout

**A project laid out the way the App Factory actually lays one out**, as opposed to
the way this codebase assumed. It exists because every other fixture here was
authored to match the reader, so the reader and the fixtures agreed and neither
agreed with the producer — and four views shipped broken against every real project.

Three differences from `with_run_state/`, each of which broke something:

| | `with_run_state/` (invented) | here (real) |
|---|---|---|
| ticket files | `tickets/CAD-125.md` | `tickets/<milestone>/<id>-<slug>.md` |
| dependency key | `dependsOn` | `depends_on` |
| roadmap | `ROADMAP.md` at the root | `docs/planning/ROADMAP.md` |

It also carries the `path` field a real manifest carries and this codebase used to
ignore, and omits the `files` field the invented fixtures carry and no real manifest
has ever written.

**Do not "fix" this fixture to look like the others.** Its whole value is that it
does not.
