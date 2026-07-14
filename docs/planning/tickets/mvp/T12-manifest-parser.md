# [T12] tickets.json manifest parser (manifest.py + MalformedManifest)

milestone: MVP · track: file-adapter · depends_on: T07, T08 · provides: `load_manifest(path)`; `manifest_entry_to_ticket_stub`; `iter_ticket_stubs(project)`; `MalformedManifest` exception (FactoryConsoleError, status 500, code `malformed_manifest`)

## Context

`tickets.json` is written by the factory and MUST be forward-compatible: unknown fields preserved on `Ticket.raw`, missing optionals default sensibly, `schemaVersion` surfaced but not enforced. Bad JSON raises `MalformedManifest` so CLI can exit 3 cleanly. Builds bare-manifest `Ticket` stubs (`id, title, status, track, milestone, dependsOn, provides, files, raw`); `bodyMarkdown/bodyHtml` are filled in later by T13.

## Staged approach

1. `file_adapter/manifest.py`.
2. `class MalformedManifest(FactoryConsoleError)`: `status=500`, `code='malformed_manifest'`; `__init__` takes `path: Path, cause: Exception | None`.
3. `load_manifest(manifest_path: Path) -> tuple[str | None, list[dict]]`: reads via `open('r', encoding='utf-8')`; parses JSON; extracts top-level `schemaVersion` + `tickets`; `JSONDecodeError -> MalformedManifest`; non-list `tickets -> MalformedManifest`.
4. `manifest_entry_to_ticket_stub(entry: dict, tickets_dir: Path) -> Ticket`: pulls id (validated by Ticket model regex), title, status, track, milestone, dependsOn, provides, files with sensible defaults; passes entire entry as `raw`; `filePath = tickets_dir / f'{id}.md'`; `bodyMarkdown/bodyHtml` default `''`.
5. `iter_ticket_stubs(project: Project) -> Iterator[Ticket]`.
6. `tests/unit/test_manifest.py`: minimal fixture -> N tickets, unknown fields preserved on `raw`, missing optionals defaulted; malformed -> `MalformedManifest`; non-list -> raises; `schemaVersion` surfaced.

## Critical files

- `server/factory_console/file_adapter/manifest.py`
- `tests/unit/test_manifest.py`

## Interface & data

Uses `open('r')` — one of the sanctioned I/O sites. Returns `Ticket` stubs. `MalformedManifest` maps to CLI exit code 3 and HTTP 500 `code=malformed_manifest`.

## Verification

`pytest tests/unit/test_manifest.py -q` green against minimal + malformed fixtures; ruff clean.
