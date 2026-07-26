"""Integration tests for the per-session loopback write token (T64).

T64 ships the MECHANISM only — the write endpoints that attach it land in T65 — so
the guarded route here is a test-local probe registered on a real ``create_app``
app, standing in for the write routes to come. The suite pins the whole contract:
``create_app`` mints a fresh token per boot and announces it on stderr (never
stdout, never a log), the probe passes only with the exact header, every failing
shape yields the same ``write_token_invalid`` 401 envelope with NO token echoed,
the existing READ routes are entirely unaffected, and the ``apiKey`` scheme is
published in ``/api/v1/openapi.json`` without any read route declaring security.

Deterministic and I/O-free: a :class:`FakeFileAdapter` over an empty ticket list,
and a pinned token wherever the assertion depends on its value.
"""

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from starlette.routing import Mount

from factory_console.api.write_token import WRITE_TOKEN_SCHEME_NAME, require_write_token
from factory_console.app import create_app
from factory_console.config import WRITE_TOKEN_HEADER
from factory_console.domain import Project
from factory_console.file_adapter import FakeFileAdapter

# The token pinned into every app whose assertions depend on its value, and a
# same-length near-miss used to prove the compare rejects anything but an exact hit.
PINNED_TOKEN = "pinned-write-token-for-tests"
WRONG_TOKEN = "pinned-write-token-for-tesXX"

# The test-local stand-in for a T65 write route: a POST guarded by the dependency
# under test and nothing else, so a 200 means the token check (not a handler) passed.
PROBE_WRITE_PATH = "/probe-write"


def _make_fake() -> FakeFileAdapter:
    """Build a minimal FakeFileAdapter over an empty ticket list."""
    project = Project(
        rootPath=Path("/proj"),
        ticketsManifestPath=Path("/proj/docs/planning/tickets.json"),
        ticketsDir=Path("/proj/docs/planning/tickets"),
        discoveredAt=datetime(2026, 1, 1),
    )
    return FakeFileAdapter(project=project, tickets=[])


def _make_app(write_token: str | None = PINNED_TOKEN) -> FastAPI:
    """Build the real app plus a write-style probe route guarded by the dependency."""
    app = create_app(
        _make_fake(),
        version="0.0.0",
        project_root=Path("/tmp/fake-root"),
        write_token=write_token,
    )

    @app.post(PROBE_WRITE_PATH, dependencies=[Depends(require_write_token)])
    def _probe_write() -> dict[str, bool]:
        return {"written": True}

    # Keep the SPA catch-all last so the probe (registered after create_app returned)
    # is matched first, exactly as production's real routes are — see the fuller
    # explanation on the same manoeuvre in test_app_factory.py.
    for mount in [r for r in app.router.routes if isinstance(r, Mount) and r.name == "static"]:
        app.router.routes.remove(mount)
        app.router.routes.append(mount)

    return app


# --------------------------------------------------------------------------- #
# Token minting + operator announcement
# --------------------------------------------------------------------------- #


def test_create_app_generates_a_distinct_token_per_app() -> None:
    # No token supplied is the normal boot path: each process mints its own secret,
    # so the token never outlives the server that printed it.
    first = create_app(_make_fake(), version="0.0.0", project_root=Path("/tmp/fake-root"))
    second = create_app(_make_fake(), version="0.0.0", project_root=Path("/tmp/fake-root"))
    assert first.state.write_token
    assert first.state.write_token != second.state.write_token


def test_create_app_uses_the_supplied_token_verbatim() -> None:
    # A pinned token (FACTORY_CONSOLE_WRITE_TOKEN or a test) wins over generation.
    app = create_app(
        _make_fake(),
        version="0.0.0",
        project_root=Path("/tmp/fake-root"),
        write_token=PINNED_TOKEN,
    )
    assert app.state.write_token == PINNED_TOKEN


def test_create_app_announces_the_token_on_stderr_only(capsys: pytest.CaptureFixture[str]) -> None:
    # The operator needs the secret on their terminal, but stdout carries the CLI's
    # machine-parsable contract line — so the announcement goes to stderr alone.
    app = create_app(_make_fake(), version="0.0.0", project_root=Path("/tmp/fake-root"))
    captured = capsys.readouterr()
    assert f"{WRITE_TOKEN_HEADER}: {app.state.write_token}" in captured.err
    assert captured.out == ""


# --------------------------------------------------------------------------- #
# The guarded write route
# --------------------------------------------------------------------------- #


def test_write_route_passes_with_the_correct_token() -> None:
    client = TestClient(_make_app())
    resp = client.post(PROBE_WRITE_PATH, headers={WRITE_TOKEN_HEADER: PINNED_TOKEN})
    assert resp.status_code == 200
    assert resp.json() == {"written": True}


@pytest.mark.parametrize(
    ("case", "headers"),
    [
        ("missing", {}),
        ("empty", {WRITE_TOKEN_HEADER: ""}),
        ("wrong", {WRITE_TOKEN_HEADER: WRONG_TOKEN}),
        # Sent as raw bytes because httpx refuses non-ASCII str header values; on the
        # server side Starlette latin-1-decodes them back into a non-ASCII str.
        ("non-ascii", {WRITE_TOKEN_HEADER: "tökén".encode()}),
    ],
)
def test_write_route_rejects_every_bad_token_shape(
    case: str, headers: dict[str, str | bytes]
) -> None:
    # All four shapes collapse to the SAME 401 envelope: a caller learns only that
    # the token was not accepted. The non-ASCII case matters because compare_digest
    # rejects non-ASCII str operands with TypeError — that must read as a mismatch,
    # never a 500.
    client = TestClient(_make_app())
    resp = client.post(PROBE_WRITE_PATH, headers=headers)
    assert resp.status_code == 401, case
    error = resp.json()["error"]
    assert error["code"] == "write_token_invalid", case
    assert WRITE_TOKEN_HEADER in error["message"], case
    # No ``details`` key: there is nothing safe to say about which token was seen.
    assert set(error) == {"code", "message"}, case


def test_rejection_never_echoes_a_token() -> None:
    # The response body must leak neither the expected secret nor the supplied guess
    # (an echo would confirm a probe and hand the token to anything reading the wire).
    client = TestClient(_make_app())
    resp = client.post(PROBE_WRITE_PATH, headers={WRITE_TOKEN_HEADER: WRONG_TOKEN})
    assert resp.status_code == 401
    assert PINNED_TOKEN not in resp.text
    assert WRONG_TOKEN not in resp.text


def test_require_write_token_raises_when_no_token_is_bound() -> None:
    # An app.state with no write token is a WIRING bug (create_app always mints one),
    # not a client-triggerable 401 — so the seam fails loudly, like get_file_writer.
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()), headers={})
    with pytest.raises(RuntimeError, match="write_token"):
        require_write_token(request)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# Read routes are untouched
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "headers",
    [{}, {WRITE_TOKEN_HEADER: WRONG_TOKEN}],
    ids=["no-header", "wrong-header"],
)
def test_read_routes_ignore_the_write_token_header(headers: dict[str, str]) -> None:
    # No global dependency is attached, so viewing the project needs no header — and
    # a bogus one is simply ignored rather than turned into a 401.
    client = TestClient(_make_app())
    for read_path in ("/api/v1/health", "/api/v1/project"):
        resp = client.get(read_path, headers=headers)
        assert resp.status_code == 200, read_path


# --------------------------------------------------------------------------- #
# Frozen OpenAPI shape (what the frontend codegen freezes against)
# --------------------------------------------------------------------------- #


def test_openapi_publishes_the_write_token_security_scheme() -> None:
    client = TestClient(_make_app())
    schema = client.get("/api/v1/openapi.json").json()
    scheme = schema["components"]["securitySchemes"][WRITE_TOKEN_SCHEME_NAME]
    assert scheme["type"] == "apiKey"
    assert scheme["in"] == "header"
    assert scheme["name"] == WRITE_TOKEN_HEADER


def test_openapi_declares_no_global_or_read_route_security() -> None:
    # Publishing the scheme must not make the whole API — or any read endpoint —
    # look authenticated to the SPA's generated client.
    client = TestClient(_make_app())
    schema = client.get("/api/v1/openapi.json").json()
    assert "security" not in schema
    assert "security" not in schema["paths"]["/api/v1/health"]["get"]
    assert "security" not in schema["paths"]["/api/v1/project"]["get"]


def test_openapi_scheme_is_stable_across_repeated_requests() -> None:
    # FastAPI caches the generated document on app.openapi_schema; the injection must
    # re-apply to that cached dict rather than being lost (or duplicated) on call two.
    client = TestClient(_make_app())
    first = client.get("/api/v1/openapi.json").json()
    second = client.get("/api/v1/openapi.json").json()
    assert first == second
    assert WRITE_TOKEN_SCHEME_NAME in second["components"]["securitySchemes"]
