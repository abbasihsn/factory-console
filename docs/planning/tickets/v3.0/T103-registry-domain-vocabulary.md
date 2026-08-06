# [T103] Registry domain vocabulary, and the store's place in the architecture

milestone: v3.0 · track: store · depends_on: T07, T102 · provides: `domain/registry.py` — `RegisteredProject`, `REGISTERED_PROJECT_ID_PATTERN`, the exhaustive `RegistryEntryCondition` union and `RegistryEntry` — plus the PROJECT_STRUCTURE/ARCHITECTURE amendment that gives `store` and `github` documented track rows.

## Context

v3.0 turns the console from a single-project viewer into a multi-project read plane, and the first
thing every other v3.0 track needs is the shared vocabulary: what a registry row IS, and what the
console is allowed to say about a registered path. ARCHITECTURE.md's v3 section already fixes the
entity as `RegisteredProject {id, name, path, addedAt}`, "distinct from the read-through `Project`
entity". This ticket lands that entity, and lands the condition union alongside it because the two
are one subject: a row, plus what the console currently knows about it.

It goes first so backend (registry endpoints) and frontend (an exhaustive label map over the
generated union) build against a written contract rather than a promise.

It also carries the structural amendment. `PROJECT_STRUCTURE.md`'s Track ownership table says
`file_adapter/` is "the ONLY layer that calls `open()`" — but that claim's whole substance is that
those reads are **read-only against the target project** (`protocol.py` says so; several modules
carry a literal READ-ONLY banner pinned by `tests/_read_only_guard.py`). The console DB is the
opposite on every axis: a different location, outside every project; a different backing store; a
writable, durable lifecycle. A new writable package would read as a violation of that sentence
unless the sentence is corrected in the same milestone, so it is corrected here. Doing all of it in
one ticket means exactly one ticket in this track touches `docs/planning/`.

**Why `RegisteredProject` and `Project` are NOT merged** — the question a reader will have, so it
goes in the module docstring. They have different lifetimes and different truth conditions.
`RegisteredProject` is a durable row asserting only "the user asked this console to track a project
at this path, under this name, since this instant". `Project` is a per-request resolution of a
project's files, constructed by `FileAdapter.load_project` and discarded at the end of the request.
Persisting the second would be persisting a claim about the filesystem that goes stale the moment a
file moves — the exact failure the condition union exists to name honestly. Merging them would also
push a console-owned writable identity into the entity every service and every fake already takes.
Resolution flows one way only: `RegisteredProject.path` → discovery → `Project`.

## Staged approach

1. CREATE `server/factory_console/domain/registry.py`. Module docstring states the
   RegisteredProject-vs-Project distinction above, and that the condition union is EXHAUSTIVE and is
   the single source of truth the SPA's generated types derive from.
2. Define `REGISTERED_PROJECT_ID_PATTERN = r"^[0-9a-f]{32}$"` (a uuid4 hex), documented as the
   single source of truth for registry-id validation the way `TICKET_ID_PATTERN` is for ticket ids —
   the API boundary and the store both import it rather than re-spelling it.
3. Define `RegisteredProject(BaseModel)`, `model_config = ConfigDict(frozen=True, extra="forbid")`
   (matching `Project`), fields `id: str` (pattern-constrained), `name: str` (min_length=1),
   `path: Path`, `addedAt: datetime`. Docstring each: `path` is ALWAYS the canonical absolute form
   the store writes (see `store/paths.py`, T106) — a consumer must not re-resolve it; `addedAt` is
   timezone-aware UTC.
4. Define `RegistryEntryCondition = Literal["ok", "path_missing", "not_a_project", "unreadable",
   "no_factory_dir"]`, following `ArtifactSkipReason`'s style in `domain/runs.py`. Docstring EACH
   member, and state the most-degraded-first precedence: `unreadable` > `path_missing` >
   `not_a_project` > `no_factory_dir` > `ok`. Spell out that `no_factory_dir` is
   degraded-but-USABLE (the project is real and browsable; only run-state/runs/spend are
   legitimately missing, per ARCHITECTURE.md's v3 per-project clarification), and that `unreadable`
   must never be folded into `not_a_project` — "I could not look" is not "I looked and there is
   nothing there", the same distinction `RunState` and `ArtifactSkipReason` already draw.
5. Define `RegistryEntry(BaseModel)` (frozen, extra-forbid) with `project: RegisteredProject` and
   `condition: RegistryEntryCondition`. Docstring: the read-time projection, the shape the REST
   layer returns; a row alone is never enough for a UI, because a row says nothing about whether its
   path still resolves. **The wire field is named `condition`, everywhere — not `availability`.**
6. Modify `server/factory_console/domain/__init__.py` to re-export the four names, `__all__` sorted
   in the existing style. AGGREGATION FILE: this is the only store-track ticket that touches it.
7. Modify `docs/planning/PROJECT_STRUCTURE.md`:
   - Narrow the `file-adapter` row from "the ONLY layer that calls `open()`" to "the only layer that
     reads the TARGET PROJECT's files".
   - Add a `store` row → `server/factory_console/store/` + `domain/registry.py`.
   - Add a `github` row → `server/factory_console/github_adapter/` + `domain/github.py` +
     `services/github_service.py` (claimed here so v3.0.1 does not silently contend for `domain/`
     and `services/`).
   - State the rule explicitly: **`domain/` is shared vocabulary — a track may ADD a new module
     there, named in its ticket, but may not edit another track's.**
   - Add `store/` and `domain/registry.py` to the tree.
8. Modify `docs/planning/ARCHITECTURE.md`: under "Data-model additions (v3)", expand
   `RegisteredProject` to the field list and ADD `RegistryEntry` + the condition union with its
   precedence. Keep it a CONTRACT statement, not an implementation walkthrough. (The REST surface
   and the shipped-vs-planned rewrite belong to T129, at the end of the milestone.)
9. CREATE `tests/unit/test_domain_registry.py`: field validation (bad id rejected, blank name
   rejected, frozen), a `model_dump()`/`model_validate` round-trip, and an exhaustiveness test
   asserting `get_args(RegistryEntryCondition)` equals the expected five-name tuple — so adding a
   sixth condition without telling the SPA fails here.

## Critical files

- `server/factory_console/domain/registry.py` (create)
- `server/factory_console/domain/__init__.py` (modify — aggregation file)
- `docs/planning/PROJECT_STRUCTURE.md` (modify)
- `docs/planning/ARCHITECTURE.md` (modify)
- `tests/unit/test_domain_registry.py` (create)

## Interface & data

Entities (NEW — this ticket is their single source of truth):
`RegisteredProject { id: str, name: str, path: Path, addedAt: datetime }` frozen/extra-forbid;
`RegistryEntry { project: RegisteredProject, condition: RegistryEntryCondition }`;
`RegistryEntryCondition = Literal["ok","path_missing","not_a_project","unreadable","no_factory_dir"]`;
`REGISTERED_PROJECT_ID_PATTERN = r"^[0-9a-f]{32}$"`.

Referenced, NOT redefined: `domain/project.py::Project` (the read-through entity this is distinct
from), `domain/ticket.py::TICKET_ID_PATTERN` (the single-source-of-truth idiom mirrored),
`domain/runs.py::ArtifactSkipReason` (the named-reason `Literal` idiom mirrored),
ARCHITECTURE.md "Data-model additions (v3)".

DB ops: none — pure models. NFR flags: **disclosure rule** — every field is explicitly modelled and
there is no `dict[str, Any]`, so `tests/integration/test_disclosure_policy.py` needs no allowlist
entry and must stay green once the backend serialises these; **MONOTONICITY** — the documented
condition precedence is the resolution rule T109 implements.

## Verification

`python -m pytest tests/unit/test_domain_registry.py -q`; then `python -m pytest -q` (full suite
must stay green — this ticket only adds models and re-exports); `make lint`. Confirm the console is
unaffected: `python -m pytest tests/integration/test_cli.py -q`.
