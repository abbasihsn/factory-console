"""Shared scaffolding for the write-endpoint integration suites.

``tests/integration/test_api_tickets_write.py`` (route semantics — status codes,
``?dryRun``, the published OpenAPI shape) and
``tests/integration/test_api_write_tickets.py`` (the boundary contract —
guard-before-port ordering, the uniform error envelope, on-disk durability) are a
deliberate split and stay separate. What they had no reason to duplicate is the
wiring underneath: the pinned token, the header both send it in, and how the ASGI
client and the filesystem-pair app get built. Those were copy-pasted, so changing
how a write app is constructed — or rotating the pinned token — meant editing two
files and silently weakening whichever one was missed.

Each suite still owns its own ``_draft_body`` / ``_edit_body`` and its own ticket
ids: those are the fixtures under test, chosen to exercise different cases, and
folding them together would only mean passing every field back as an override.

This is a test helper, not a test module — the leading underscore keeps pytest from
collecting it, and it imports as a top-level module via the ``tests`` entry in
``[tool.pytest.ini_options].pythonpath``.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from factory_console.app import create_app
from factory_console.config import WRITE_TOKEN_HEADER
from factory_console.domain.write import TicketContentFields
from factory_console.file_adapter.real import RealFileAdapter
from factory_console.file_adapter.real_writer import RealFileWriter
from factory_console.store.registry_protocol import ProjectRegistry

WITH_RUN_STATE = Path(__file__).resolve().parent / "fixtures" / "projects" / "with_run_state"
"""The checked-in staged-run-state fixture. Read-only — always copy before writing."""

PINNED_TOKEN = "pinned-write-token-for-tests"
"""Pinned so request headers can name the exact token ``create_app`` bound.

Also gives the "no secret in any error body" assertions a concrete string to search
for. Defined once here so the header value and the bound value cannot drift apart.
"""

WRONG_TOKEN = "pinned-write-token-for-tesXX"
"""Same length as :data:`PINNED_TOKEN`, differing only in the last two characters.

Length-equal on purpose: the comparison is constant-time, and a wrong token of the
same length exercises that path rather than an early length mismatch.
"""

AUTH = {WRITE_TOKEN_HEADER: PINNED_TOKEN}
"""The one header that authorizes a write, carrying :data:`PINNED_TOKEN`."""


def app_over(root: Path, *, registry: ProjectRegistry | None = None) -> FastAPI:
    """Build the app over the filesystem pair, both ports rooted at ``root``.

    Rooting the adapter and the writer at the same tree is what makes an adapter read
    after a writer apply observe the written state — the coupling production has and
    the in-memory pair does not.

    ``root`` and ``registry`` are the selection seam's two inputs (as in
    ``test_api_tickets.py``'s ``_fake_app`` and ``test_api_runs.py``'s ``_app``).
    Leaving ``registry`` unset is PINNED MODE — the selection can never leave ``root``,
    which is what every write case that only cares about one project wants. Passing one
    lets a case drive the SELECTED project via ``app.state.selection.select(row.id)``,
    which is the only way to prove a write lands in the selected tree rather than the
    pinned one.
    """
    return create_app(
        RealFileAdapter(),
        version="0.0.0",
        project_root=root,
        file_writer=RealFileWriter(),
        write_token=PINNED_TOKEN,
        project_registry=registry,
    )


def fixture_copy(dest: Path) -> Path:
    """Copy the checked-in fixture project to ``dest`` and return that path.

    The copy is what makes a write test safe: the checked-in fixture is shared by the
    read-side suites and must never be mutated. Exposed separately from
    :func:`real_app` because a multi-project case needs TWO independent trees under one
    app, which a helper that also builds the app cannot give it.
    """
    shutil.copytree(WITH_RUN_STATE, dest)
    return dest


def real_app(tmp_path: Path, *, registry: ProjectRegistry | None = None) -> tuple[FastAPI, Path]:
    """Build the filesystem-pair app over a THROWAWAY copy of the fixture.

    ``registry`` is forwarded to :func:`app_over`; the default ``None`` is pinned mode.
    """
    root = fixture_copy(tmp_path / "project")
    return app_over(root, registry=registry), root


def client(app: FastAPI) -> AsyncClient:
    """An ``httpx.AsyncClient`` speaking ASGI directly to ``app`` (no socket)."""
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://127.0.0.1")


def content_fields(ticket_id: str, **overrides: object) -> TicketContentFields:
    """A valid set of the five App Factory v3 CONTENT fields, for seeding a write test.

    Every field is required and two carry ``minItems: 1``, so there is no such thing as a
    partially-seeded v3 ticket — which is why this exists rather than each suite spelling
    five keyword arguments per fixture. ``ticket_id`` is woven into the prose so a test
    asserting on rendered output can tell two seeded tickets apart.

    Shared across the write suites the way the app wiring above is, and for the same
    reason: this is scaffolding, not the fixture under test. A suite that cares about a
    particular field overrides it by name.
    """
    fields: dict[str, object] = {
        "context": f"Why {ticket_id} exists.",
        "approach": f"1. Build {ticket_id}.\n2. Verify it.",
        "criticalFiles": [f"src/{ticket_id}.py"],
        "interfaceData": "N/A",
        "verificationCommands": [f"pytest tests/{ticket_id} -q"],
    }
    fields.update(overrides)
    return TicketContentFields(**fields)  # type: ignore[arg-type]


def seeded_contents(*ticket_ids: str) -> dict[str, TicketContentFields]:
    """``{id: content_fields(id)}`` for each id — the ``contents=`` seed a fake writer takes."""
    return {ticket_id: content_fields(ticket_id) for ticket_id in ticket_ids}
