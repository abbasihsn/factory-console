"""Integration tests for the real ``create_app`` application factory.

Drive an app built over a :class:`FakeFileAdapter` with FastAPI's ``TestClient``
and pin the cross-cutting seams T20 installs: the ``/api/v1/openapi.json`` schema,
the ``Depends(get_file_adapter)`` DI wiring, the domain/validation exception
handlers reachable through probe routes mounted on the created app, and exactly
one ``factory_console.access`` log line per request. Deterministic and I/O-free —
the fake is seeded with an empty ticket list; no filesystem is touched.
"""

import logging
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi import Path as PathParam
from fastapi.testclient import TestClient
from pydantic import BaseModel
from starlette.routing import Mount

from factory_console.api.deps import (
    get_file_adapter,
    get_file_watcher,
    get_file_writer,
    get_run_artifact_reader,
)
from factory_console.app import _SpaStaticFiles, create_app
from factory_console.domain import TICKET_ID_PATTERN, Project
from factory_console.file_adapter import FakeFileAdapter
from factory_console.file_adapter.discovery import ProjectNotFound
from factory_console.file_adapter.fake_runs import FakeRunArtifactReader
from factory_console.file_adapter.fake_writer import FakeFileWriter


class _Body(BaseModel):
    """Minimal request body used to trigger a non-ticket-id validation error."""

    count: int


def _make_fake() -> FakeFileAdapter:
    """Build a minimal FakeFileAdapter over an empty ticket list."""
    project = Project(
        rootPath=Path("/proj"),
        ticketsManifestPath=Path("/proj/docs/planning/tickets.json"),
        ticketsDir=Path("/proj/docs/planning/tickets"),
        discoveredAt=datetime(2026, 1, 1),
    )
    return FakeFileAdapter(project=project, tickets=[])


def _make_app(fake: FakeFileAdapter | None = None) -> FastAPI:
    """Build the real app plus probe routes exercising each cross-cutting seam."""
    app = create_app(
        fake or _make_fake(),
        version="0.0.0",
        project_root=Path("/tmp/fake-root"),
    )

    @app.get("/probe")
    def _probe() -> None:
        raise ProjectNotFound(Path("/x"))

    @app.post("/probe-body")
    def _probe_body(body: _Body) -> dict[str, int]:
        return {"count": body.count}

    @app.get("/probe/{ticket_id}")
    def _probe_ticket(ticket_id: str = PathParam(..., pattern=TICKET_ID_PATTERN)) -> dict[str, str]:
        return {"ticketId": ticket_id}

    @app.get("/probe-boom")
    def _probe_boom() -> None:
        # A genuinely unhandled exception (not a FactoryConsoleError) so it escapes
        # the registered handlers and becomes a 500 via ServerErrorMiddleware.
        raise RuntimeError("boom")

    # create_app mounts the SPA catch-all at "/" LAST — in production every real
    # route is registered before it, so it only ever serves paths nothing else
    # matched. These probe routes stand in for those real routes but are added after
    # create_app returns, so when a packaged _static/ is present on disk the catch-all
    # would shadow them (200/405 instead of the handler). Move the mount back to last
    # (a no-op when no _static/ is bundled) so the probes match first, as production's
    # real routes do.
    static_mounts = [r for r in app.router.routes if isinstance(r, Mount) and r.name == "static"]
    for mount in static_mounts:
        app.router.routes.remove(mount)
        app.router.routes.append(mount)

    return app


def test_openapi_returns_valid_v3_document() -> None:
    client = TestClient(_make_app())
    resp = client.get("/api/v1/openapi.json")
    assert resp.status_code == 200
    schema = resp.json()
    assert schema["openapi"].startswith("3")
    assert "/api/v1/health" in schema["paths"]


def test_get_file_adapter_returns_the_adapter_bound_by_create_app() -> None:
    # create_app stashes the adapter on app.state; the DI provider reads it back so
    # handlers get the exact instance without importing a concrete adapter.
    fake = _make_fake()
    app = _make_app(fake)
    request = SimpleNamespace(app=app)
    assert get_file_adapter(request) is fake  # type: ignore[arg-type]


def test_unhandled_project_not_found_maps_to_404_envelope() -> None:
    client = TestClient(_make_app())
    resp = client.get("/probe")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "project_not_found"


def test_request_validation_error_maps_to_422() -> None:
    client = TestClient(_make_app())
    resp = client.post("/probe-body", json={})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_error"


def test_ticket_id_pattern_violation_maps_to_400_invalid_ticket_id() -> None:
    client = TestClient(_make_app())
    resp = client.get("/probe/bad id")
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "invalid_ticket_id"


def test_exactly_one_access_log_line_per_request(caplog: pytest.LogCaptureFixture) -> None:
    client = TestClient(_make_app())
    caplog.set_level(logging.INFO, logger="factory_console.access")
    resp = client.get("/api/v1/health")
    assert resp.status_code == 200
    records = [record for record in caplog.records if record.name == "factory_console.access"]
    assert len(records) == 1
    message = records[0].getMessage()
    assert "GET" in message
    assert "/api/v1/health" in message
    assert "200" in message


def test_access_log_line_emitted_for_unhandled_500(caplog: pytest.LogCaptureFixture) -> None:
    # The one-line-per-request invariant must hold even when the handler raises an
    # unhandled exception (turned into a 500 by ServerErrorMiddleware): exactly one
    # access line, carrying status 500, is still emitted.
    client = TestClient(_make_app(), raise_server_exceptions=False)
    caplog.set_level(logging.INFO, logger="factory_console.access")
    resp = client.get("/probe-boom")
    assert resp.status_code == 500
    records = [record for record in caplog.records if record.name == "factory_console.access"]
    assert len(records) == 1
    message = records[0].getMessage()
    assert "GET" in message
    assert "/probe-boom" in message
    assert "500" in message


def test_get_file_watcher_returns_the_watcher_bound_by_create_app() -> None:
    # create_app stashes the optional watcher on app.state; the DI provider reads
    # it back so the SSE endpoint gets the exact instance the composition root wired.
    watcher = object()
    app = create_app(
        _make_fake(),
        version="0.0.0",
        project_root=Path("/tmp/fake-root"),
        file_watcher=watcher,  # type: ignore[arg-type]
    )
    request = SimpleNamespace(app=app)
    assert get_file_watcher(request) is watcher  # type: ignore[arg-type]


def test_get_file_watcher_returns_none_when_no_watcher_wired() -> None:
    # The watcher is opt-in: an app built without one is a valid configuration, so
    # the provider returns None (degrade gracefully) rather than raising.
    app = _make_app()
    request = SimpleNamespace(app=app)
    assert get_file_watcher(request) is None  # type: ignore[arg-type]


def test_get_file_watcher_returns_none_when_state_unset() -> None:
    # Even on an app.state with no file_watcher attribute at all, the provider
    # returns None rather than raising (unlike get_file_adapter's wiring guard).
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))
    assert get_file_watcher(request) is None  # type: ignore[arg-type]


def test_get_file_adapter_raises_when_unbound() -> None:
    # Directly exercise the DI seam's guard: an app.state without a bound adapter
    # is a wiring bug, so the provider must fail loudly rather than return None.
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))
    with pytest.raises(RuntimeError, match="file_adapter"):
        get_file_adapter(request)  # type: ignore[arg-type]


def test_get_file_writer_returns_the_writer_bound_by_create_app() -> None:
    # create_app stashes the optional write-core writer on app.state; the DI
    # provider reads it back so a write handler gets the exact instance the
    # composition root wired, without importing a concrete writer.
    writer = FakeFileWriter(manifest=[])
    app = create_app(
        _make_fake(),
        version="0.0.0",
        project_root=Path("/tmp/fake-root"),
        file_writer=writer,
    )
    request = SimpleNamespace(app=app)
    assert get_file_writer(request) is writer  # type: ignore[arg-type]


def test_get_file_writer_raises_when_unbound() -> None:
    # The writer seam mirrors the adapter's guard (not the opt-in watcher): an
    # app.state without a bound writer is a wiring bug, so the provider raises
    # rather than returning None.
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))
    with pytest.raises(RuntimeError, match="file_writer"):
        get_file_writer(request)  # type: ignore[arg-type]


def test_get_run_artifact_reader_returns_the_reader_bound_by_create_app() -> None:
    # The runs seam is wired exactly like the writer's: create_app stashes the
    # reader on app.state and the DI provider reads back that same instance, so a
    # runs handler never imports a concrete reader.
    reader = FakeRunArtifactReader()
    app = create_app(
        _make_fake(),
        version="0.0.0",
        project_root=Path("/tmp/fake-root"),
        run_artifact_reader=reader,
    )
    request = SimpleNamespace(app=app)
    assert get_run_artifact_reader(request) is reader  # type: ignore[arg-type]


def test_get_run_artifact_reader_raises_when_unbound() -> None:
    # Same guard as the adapter and writer seams, pinned here because it is the
    # whole reason this provider raises instead of returning None: an unbound
    # reader is a wiring bug, not a degraded mode the runs endpoints can serve.
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))
    with pytest.raises(RuntimeError, match="run_artifact_reader"):
        get_run_artifact_reader(request)  # type: ignore[arg-type]


def _spa_client(tmp_path: Path) -> TestClient:
    """Build a TestClient over a ``_SpaStaticFiles`` mount seeded with a fake bundle.

    Mirrors the real mount: a probe under the ``/api/v1`` prefix registered BEFORE
    the ``/`` mount (so a known API route is matched first), the SPA bundle mounted
    last. ``_static/`` is gitignored and absent in a dev checkout, so the fallback is
    exercised against a synthesized bundle rather than the packaged one.
    """
    (tmp_path / "index.html").write_text("<!doctype html><title>Factory Console SPA</title>")
    (tmp_path / "app.js").write_text("export const boot = 1;\n")
    app = FastAPI()

    @app.get("/api/v1/health")
    def _health() -> dict[str, bool]:
        return {"ok": True}

    app.mount("/", _SpaStaticFiles(directory=str(tmp_path), html=True), name="static")
    return TestClient(app)


def test_spa_static_serves_real_assets_and_root(tmp_path: Path) -> None:
    client = _spa_client(tmp_path)

    asset = client.get("/app.js")
    assert asset.status_code == 200
    assert "export const boot" in asset.text

    root = client.get("/")
    assert root.status_code == 200
    assert "Factory Console SPA" in root.text


def test_spa_static_falls_back_to_index_for_deep_links(tmp_path: Path) -> None:
    # A hard refresh / bookmark / shared link to a client route (never a real file
    # on disk) must return index.html (200) so the SPA router can resolve it, not a
    # blank 404.
    client = _spa_client(tmp_path)
    for deep_link in ("/tickets/T31", "/tickets/T31/deps"):
        resp = client.get(deep_link)
        assert resp.status_code == 200, deep_link
        assert "Factory Console SPA" in resp.text


def test_spa_static_does_not_swallow_unknown_api_paths(tmp_path: Path) -> None:
    # A known API route is matched before the mount; an UNKNOWN /api/v1 path falls
    # through to the static mount but must keep its 404 rather than masquerade as the
    # SPA shell.
    client = _spa_client(tmp_path)

    assert client.get("/api/v1/health").json() == {"ok": True}

    unknown = client.get("/api/v1/does-not-exist")
    assert unknown.status_code == 404
    assert "Factory Console SPA" not in unknown.text
