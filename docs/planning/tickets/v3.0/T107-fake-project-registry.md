# [T107] FakeProjectRegistry + the shared port-contract suite

milestone: v3.0 · track: store · depends_on: T106, T10 · provides: `store/fake_registry.py` — a side-effect-free in-memory `ProjectRegistry` with injectable `id_factory` and `clock` — plus `tests/_registry_contract.py`, the shared conformance suite BOTH implementations are run against so the two can never drift.

## Context

Every backend integration test for the v3.0 registry endpoints needs a registry that answers
correctly and touches nothing — the role `FakeFileAdapter` plays for reads and `FakeFileWriter` for
writes. This lands before the SQLite implementation so the backend track is unblocked by a fake
rather than by a db, and so the conformance suite exists before there is a second implementation to
hold to it.

**The suite is the substantive part.** `FakeFileAdapter` and `RealFileAdapter` have already drifted
once in this repo in a way that mattered — the fake answers `unknown` where the real one answers
`absent`, and the docstring has to warn readers about it — because their behaviours were pinned by
separate tests. A registry drift would be worse, because the divergences available here are
duplicate detection, canonicalization and selection-on-delete, all of which a fake can get
"reasonably" wrong and pass its own tests. So the behavioural assertions live in one parametrizable
helper module and both implementations are run through it. It is a helper, not a collected test
module, so it takes the leading-underscore name `_read_only_guard.py` already established, and
imports as a top-level module via pytest's `pythonpath = ["server", "tests"]`.

## Staged approach

1. CREATE `server/factory_console/store/fake_registry.py`. Module docstring modelled on `fake.py`:
   in-memory, no filesystem access, no mutation of caller-supplied values, satisfies
   `ProjectRegistry` structurally (no inheritance).
2. `class FakeProjectRegistry` with `__init__(self, projects: list[RegisteredProject] | None = None,
   *, selected_id: str | None = None, id_factory: Callable[[], str] | None = None,
   clock: Callable[[], datetime] | None = None)`. Defaults: `uuid4().hex` and
   `lambda: datetime.now(UTC)`. Document the two seams as the reason a test can assert exact ids and
   timestamps without freezing global time.
3. Store rows in a dict keyed by id, preserving insertion order, plus a `path -> id` index keyed by
   the CANONICAL path so duplicate detection uses the same rule the real one does — reuse
   `store.paths.canonical_project_path`, do not re-implement it.
4. Implement all seven methods to the port's documented semantics:
   - `add_project` canonicalizes, defaults the name, raises `DuplicateProjectPath` (with
     `existingId`) and propagates `InvalidProjectPath`;
   - `list_projects` returns a NEW list sorted by `(addedAt, id)`;
   - `find_by_path` canonicalizes;
   - `remove_project` returns bool AND clears the selection when it removed the selected row
     (mirroring the schema's `ON DELETE SET NULL` — **the highest-value fake/real agreement in the
     port**);
   - `get_selected_project` returns None with no fallback;
   - `set_selected_project` raises `ProjectNotRegistered` for an unknown id and accepts None to
     clear.
5. CREATE `tests/_registry_contract.py`: a module docstring explaining it is a shared helper, not a
   collected test, and why (the FakeFileAdapter/RealFileAdapter drift above). Export
   `assert_registry_conforms(make_registry: Callable[[], ProjectRegistry]) -> None` covering:
   empty registry → `[]` and selection None; add then list; add returns an id matching
   `REGISTERED_PROJECT_ID_PATTERN` and a timezone-aware `addedAt`; the stored path is canonical; a
   second add of a differently-spelled but equal path raises `DuplicateProjectPath` naming the
   existing id; a nested-inside-another-project path is accepted; a relative path raises
   `InvalidProjectPath`; `get_project`/`find_by_path` hit and miss; `remove_project` True then False;
   select then get; select an unknown id raises `ProjectNotRegistered`;
   `set_selected_project(None)` clears; **removing the selected project clears the selection**; a
   registered path that does not exist on disk round-trips unharmed; `isinstance(registry,
   ProjectRegistry)` holds.
6. CREATE `tests/unit/test_fake_registry.py`: run the shared contract against
   `FakeProjectRegistry`, plus fake-specific tests for the injected `id_factory`/`clock` and for the
   no-shared-mutable-state property (mutating a returned list does not affect the registry).
7. Do NOT touch `store/__init__.py` — consumers import
   `factory_console.store.fake_registry.FakeProjectRegistry` by full path.

## Critical files

- `server/factory_console/store/fake_registry.py` (create)
- `tests/_registry_contract.py` (create — shared helper, not collected)
- `tests/unit/test_fake_registry.py` (create)

## Interface & data

`FakeProjectRegistry(projects=None, *, selected_id=None, id_factory=None, clock=None)` implementing
every `ProjectRegistry` method with identical signatures and identical raise conditions.
Test helper: `tests/_registry_contract.py::assert_registry_conforms(make_registry)`.

Referenced, not redefined: `store/registry_protocol.py::ProjectRegistry` and its three errors,
`store/paths.py::canonical_project_path`, `domain/registry.py::RegisteredProject`,
`file_adapter/fake.py` (the fake's shape and docstring conventions), `tests/_read_only_guard.py`
(the underscore-helper convention).

DB ops: none — the fake performs no I/O of any kind, which is the property under test.
NFR flags: determinism via injected id/clock seams; **no filesystem access** — a fake that touched
disk would reproduce the failure mode where a fake-backed test silently reads the host filesystem.

## Verification

`python -m pytest tests/unit/test_fake_registry.py -q`;
`python -m pytest -q --cov=factory_console` (85% gate); `make lint`.
Confirm the helper is not collected: `python -m pytest tests -q --collect-only` must list no test
from `tests/_registry_contract.py`.
