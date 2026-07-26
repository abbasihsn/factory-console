"""The write-side auth seam: verify the per-session ``X-Factory-Write-Token``.

The console stays loopback-only, but a v2 write crosses a state-changing boundary, so
every mutation additionally presents a secret bound once per server start:
``create_app`` mints one, or takes the operator's ``FACTORY_CONSOLE_WRITE_TOKEN`` pin,
stashes it on ``app.state.write_token``, and announces the header on stderr — echoing
the value only when it generated it. :func:`require_write_token` is the dependency the
write router attaches — and the ONLY place that compares the supplied header against
that secret; read routes never reference it, so the SPA's viewing flows stay
header-free.

:class:`WriteTokenInvalid` is the single 401 the seam raises. Its message names the
header and nothing else: neither the supplied nor the expected token is ever echoed
into a response body, a log line, or an exception string.

:func:`publish_write_token_scheme` documents the header as an ``apiKey`` security
scheme in the app's OpenAPI document — without attaching a global security requirement
that would make every read route look authenticated. The three write routes in
:mod:`factory_console.api.v1.tickets_write` name that scheme on their own operations
(see :func:`publish_write_token_scheme` for why they must), so the document describes
not just the header but exactly which operations demand it, and no read operation
appears to. It reaches no generated code, though: the SPA's codegen is
``openapi-typescript``, which omits ``securitySchemes`` outright, so
``frontend/src/lib/api/types.ts`` gains nothing and the SPA sets the header by hand.
"""

from __future__ import annotations

import secrets
from typing import Any

from fastapi import FastAPI, Request
from fastapi.security import APIKeyHeader

from factory_console.config import WRITE_TOKEN_HEADER
from factory_console.errors import FactoryConsoleError

# The ``components.securitySchemes`` key the scheme is published under, and the name
# the OpenAPI document identifies the write-token scheme by.
WRITE_TOKEN_SCHEME_NAME = "FactoryWriteToken"

# FastAPI's own ``apiKey``-in-header model, used purely to BUILD the OpenAPI scheme
# object (not as a route dependency — :func:`require_write_token` reads the header
# itself so that the loopback token check has exactly one implementation).
_WRITE_TOKEN_SCHEME = APIKeyHeader(
    name=WRITE_TOKEN_HEADER,
    scheme_name=WRITE_TOKEN_SCHEME_NAME,
    # Must hold for BOTH provenances, since this text ships in openapi.json (the app
    # serves no /docs or /redoc): a generated token is printed and lasts one process, a
    # pinned one is neither printed nor regenerated. Wording that named only the
    # generated case would be false for every operator who sets the env var.
    description=(
        "Write token for this server session: minted at startup and printed to the "
        "server's stderr, or pinned via FACTORY_CONSOLE_WRITE_TOKEN. Required on "
        "write routes only."
    ),
    auto_error=False,
)


class WriteTokenInvalid(FactoryConsoleError):
    """The request did not present the session's write token.

    Covers every failing shape identically — header absent, header empty, header
    wrong — at HTTP 401, so a caller learns only that the token was not accepted.
    The message names the required header (public information) and carries no
    ``details``; the supplied and expected tokens are deliberately never included,
    which is also why there is no per-case message.
    """

    def __init__(self) -> None:
        super().__init__(
            code="write_token_invalid",
            message=f"Missing or invalid {WRITE_TOKEN_HEADER} header",
            status=401,
        )


def _tokens_match(supplied: str, expected: str) -> bool:
    """Return whether ``supplied`` equals ``expected``, compared in constant time.

    Both sides are UTF-8 encoded before :func:`secrets.compare_digest` because its
    ``str`` overload accepts ASCII-only operands and raises :class:`TypeError`
    otherwise — a non-ASCII header value is attacker-controlled input and must read
    as a plain mismatch, never a 500. Comparing the bytes keeps the check
    constant-time (only the supplied value's length leaks, never its content).
    """
    return secrets.compare_digest(supplied.encode("utf-8"), expected.encode("utf-8"))


def require_write_token(request: Request) -> None:
    """Verify the request's ``X-Factory-Write-Token`` against the session token.

    The dependency every write route attaches (``Depends(require_write_token)``);
    it returns ``None`` on success and raises on failure, so a handler that runs at
    all has already been authorized.

    Reads the expected token from ``request.app.state.write_token``, which
    ``create_app`` always sets. An unbound or empty value there is a WIRING bug —
    an app built outside the factory — not a client-triggerable condition, so it
    raises :class:`RuntimeError` exactly like the :func:`get_file_adapter` /
    :func:`get_file_writer` seams rather than degrading to a 401. Either way the
    request fails closed: an unconfigured token can never let a write through.

    Raises:
        WriteTokenInvalid: The header is missing, empty, or does not match (401).
        RuntimeError: No write token is bound on ``app.state`` (a wiring bug).
    """
    expected = getattr(request.app.state, "write_token", None)
    if not expected:
        raise RuntimeError(
            "No write token bound on app.state.write_token; "
            "build the app with create_app(...), which always mints one."
        )
    supplied = request.headers.get(WRITE_TOKEN_HEADER)
    if not supplied or not _tokens_match(supplied, expected):
        raise WriteTokenInvalid()


def publish_write_token_scheme(app: FastAPI) -> None:
    """Add the write-token ``apiKey`` security scheme to ``app``'s OpenAPI document.

    Write routes depend on :func:`require_write_token`, a plain dependency FastAPI
    cannot derive a security scheme from, and read routes must stay completely
    header-free — so the scheme is injected into ``components.securitySchemes``
    rather than inferred from the routes, and no global security requirement is
    added. The document therefore describes the header without any read endpoint
    appearing to require it.

    This is documentation, not wiring: ``openapi-typescript`` (the SPA's codegen)
    drops ``securitySchemes``, so nothing here reaches the generated types and the
    SPA sends the header by hand.

    Publishing the scheme is only half the description on its own. Because
    :func:`require_write_token` is a plain dependency rather than a
    :class:`~fastapi.security.base.SecurityBase`, FastAPI will not stamp a ``security``
    requirement on the operations that use it, so each write route says so itself —
    ``openapi_extra={"security": [{WRITE_TOKEN_SCHEME_NAME: []}]}`` in
    :mod:`factory_console.api.v1.tickets_write` — or the document would name a header
    that no operation requires.

    Wraps ``app.openapi`` instead of building the document eagerly, so routes
    registered after ``create_app`` returns are still included. FastAPI caches the
    generated document on ``app.openapi_schema``, so the injection re-applies to the
    same dict on later calls and stays idempotent.
    """
    build_schema = app.openapi

    def openapi_with_write_token_scheme() -> dict[str, Any]:
        """Return the app's OpenAPI document with the write-token scheme added."""
        schema = build_schema()
        components = schema.setdefault("components", {})
        schemes = components.setdefault("securitySchemes", {})
        schemes[WRITE_TOKEN_SCHEME_NAME] = _WRITE_TOKEN_SCHEME.model.model_dump(
            by_alias=True, exclude_none=True, mode="json"
        )
        return schema

    app.openapi = openapi_with_write_token_scheme  # type: ignore[method-assign]
