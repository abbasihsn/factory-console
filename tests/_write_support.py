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
from factory_console.file_adapter.real import RealFileAdapter
from factory_console.file_adapter.real_writer import RealFileWriter

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


def app_over(root: Path) -> FastAPI:
    """Build the app over the filesystem pair, both ports rooted at ``root``.

    Rooting the adapter and the writer at the same tree is what makes an adapter read
    after a writer apply observe the written state — the coupling production has and
    the in-memory pair does not.
    """
    return create_app(
        RealFileAdapter(),
        version="0.0.0",
        project_root=root,
        file_writer=RealFileWriter(),
        write_token=PINNED_TOKEN,
    )


def real_app(tmp_path: Path) -> tuple[FastAPI, Path]:
    """Build the filesystem-pair app over a THROWAWAY copy of the fixture.

    The copy is what makes a write test safe: the checked-in fixture is shared by the
    read-side suites and must never be mutated.
    """
    root = tmp_path / "project"
    shutil.copytree(WITH_RUN_STATE, root)
    return app_over(root), root


def client(app: FastAPI) -> AsyncClient:
    """An ``httpx.AsyncClient`` speaking ASGI directly to ``app`` (no socket)."""
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
