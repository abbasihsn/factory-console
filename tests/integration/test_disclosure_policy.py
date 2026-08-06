"""The disclosure rule, enforced over the WHOLE published contract rather than per route.

``ARCHITECTURE.md`` ("Other factory artefacts (read-only)") states one rule for every
read-only endpoint: a response MUST NOT serialise an unmodelled, factory-written
artefact verbatim; it may disclose only the specific fields a real consumer needs,
declared by name at the point of disclosure and covered by a test. This file is that
test, and it is deliberately attached to the RULE and not to today's two endpoints.

That is the whole point (T102). ``/spend`` and ``/runs`` were built in the same version
and took opposite positions — one refused to project an 80-character excerpt of a file
it fully models, the other forwarded every key of files it explicitly does not — because
each was internally consistent and nothing checked them against a shared rule. A test
naming ``/runs`` and its two field names would have caught neither, and would not catch
a THIRD endpoint that surfaces ``.factory/last-stop.json`` next year by copying whichever
neighbour it happens to read. So the assertion is structural: walk the response schemas
the app actually publishes and fail on any free-form object — ``dict[str, Any]`` — that
is not on the reviewed allowlist below.

RESPONSE schemas only, reached transitively from the routes' declared 200 bodies.
Request bodies are excluded on purpose: ``TicketDraft.frontMatter`` is arbitrary YAML the
CALLER supplies, and accepting an unmodelled object inbound discloses nothing. The rule
is about what leaves the process.

Uses ``app.openapi()`` rather than the ``response_model`` annotations because the
published document is what a client is entitled to, and it is what
``frontend/src/lib/api/types.ts`` is generated from — so a shape that reaches the
frontend cannot reach it unseen by this file.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from factory_console.app import create_app
from factory_console.domain import Project
from factory_console.file_adapter import FakeFileAdapter
from factory_console.file_adapter.run_artifacts import FakeRunArtifactReader

# The property paths — ``<Schema>.<field>`` — allowed to publish a free-form object.
#
# Named individually and kept SHORT, because every entry is a standing exception to the
# rule above and must be re-argued to be added. It holds no artefact payload, which is
# the property under test: the one member is the tickets MANIFEST entry, a planning
# document this repo's own contract says to preserve unknown keys of ("Schema tolerance
# (manifest)" in ``ARCHITECTURE.md``) so a console reading a newer factory's manifest
# neither drops nor hides fields it has not heard of. That file is not written by a lane
# and carries no run metrics; it is the thing the operator wrote.
#
# A ``.factory/`` artefact NEVER belongs here — the rule's answer for one is a declared
# field allowlist at the boundary (``DISCLOSED_ARTIFACT_FIELDS`` in
# ``api/v1/runs.py``), not an exemption.
ALLOWED_FREE_FORM_PROPERTIES: frozenset[str] = frozenset({"Ticket.raw"})

FAKE_ROOT = Path("/factory/demo-project")


def _app() -> Any:
    project = Project(
        rootPath=FAKE_ROOT,
        ticketsManifestPath=FAKE_ROOT / "docs/planning/tickets.json",
        ticketsDir=FAKE_ROOT / "docs/planning/tickets",
        roadmapPath=FAKE_ROOT / "ROADMAP.md",
        runStateDir=FAKE_ROOT / ".factory/run-state",
        discoveredAt=datetime(2026, 8, 6, 12, 0, 0),
    )
    return create_app(
        FakeFileAdapter(project=project, tickets=[]),
        version="0.0.0",
        project_root=FAKE_ROOT,
        run_artifact_reader=FakeRunArtifactReader(),
    )


def _component_name(ref: str) -> str | None:
    """The component schema a ``$ref`` names, or ``None`` for any other pointer."""
    prefix = "#/components/schemas/"
    return ref[len(prefix) :] if ref.startswith(prefix) else None


def _refs_in(node: Any) -> set[str]:
    """Every component name referenced anywhere inside one schema node.

    A blunt recursive walk of the JSON rather than a keyword-driven one (``properties``,
    ``items``, ``anyOf``, ``$defs``, …): the enumeration is what goes stale, and a
    composition keyword this file has not heard of would silently drop a whole subtree
    from the sweep — a schema exempted by an omission, which is precisely the failure
    mode being guarded against.
    """
    found: set[str] = set()
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str) and (name := _component_name(ref)) is not None:
            found.add(name)
        for value in node.values():
            found |= _refs_in(value)
    elif isinstance(node, list):
        for value in node:
            found |= _refs_in(value)
    return found


def _response_schema_names(document: dict[str, Any]) -> set[str]:
    """Every component schema reachable from a declared JSON response body."""
    seen: set[str] = set()
    pending: set[str] = set()
    for operations in document["paths"].values():
        for operation in operations.values():
            if not isinstance(operation, dict):
                continue
            for response in operation.get("responses", {}).values():
                content = response.get("content", {})
                for media in content.values():
                    pending |= _refs_in(media.get("schema", {}))
    components = document.get("components", {}).get("schemas", {})
    while pending:
        name = pending.pop()
        if name in seen:
            continue
        seen.add(name)
        pending |= _refs_in(components.get(name, {}))
    return seen


def _is_free_form_object(schema: Any) -> bool:
    """Whether this schema REACHES a bare ``dict[str, Any]``: directly, or nested inside it.

    ``additionalProperties`` true (Pydantic's rendering of a ``dict[str, Any]`` value)
    with no ``properties`` of its own. A ``dict[str, str]`` renders
    ``additionalProperties: {"type": "string"}`` and is NOT itself free-form: its
    values are typed, and — the point — the keys it may carry are whatever the code
    that built it put there, which for ``ProjectedArtifactRead`` is a declared
    allowlist.

    Recurses the same way :func:`_refs_in` does, and for the same reason: a single-
    node test only catches a free-form object sitting directly on a property, and
    misses one hiding one level down — ``list[dict[str, Any]]`` (an ``items``
    schema) or ``dict[str, dict[str, Any]]`` (a nested ``additionalProperties``
    schema) both pass a single-node test cleanly while publishing exactly the
    unconstrained payload the rule exists to catch. So this walks ``anyOf``/
    ``oneOf``/``allOf`` (composition, including the union Pydantic emits for an
    optional field), ``items``/``prefixItems`` (array shape) and a nested
    ``additionalProperties`` SCHEMA (as opposed to the bare ``True`` above) — every
    keyword through which a schema can hold another schema.

    Deliberately does NOT flag a shapeless ``object`` with neither ``properties``
    nor ``additionalProperties`` at all (a bare untyped field): that shape is also
    how FastAPI's own built-in ``ValidationError.ctx`` renders, on the automatic
    422 response every route gets — a framework schema no ticket here authored
    and no factory artefact flows through, so flagging it would fail the whole
    suite over something this rule was never about disclosing.
    """
    if not isinstance(schema, dict):
        return False
    for branch in schema.get("anyOf", []) + schema.get("oneOf", []) + schema.get("allOf", []):
        if _is_free_form_object(branch):
            return True
    if schema.get("type") == "array":
        if _is_free_form_object(schema.get("items", {})):
            return True
        return any(_is_free_form_object(item) for item in schema.get("prefixItems", []))
    if schema.get("type") == "object":
        additional = schema.get("additionalProperties")
        if isinstance(additional, dict):
            return _is_free_form_object(additional)
        return additional is True and not schema.get("properties")
    return False


def _free_form_properties(document: dict[str, Any]) -> set[str]:
    """``<Schema>.<field>`` for every free-form object on a reachable RESPONSE schema."""
    components = document.get("components", {}).get("schemas", {})
    offenders: set[str] = set()
    for name in _response_schema_names(document):
        schema = components.get(name, {})
        for field, property_schema in schema.get("properties", {}).items():
            if _is_free_form_object(property_schema):
                offenders.add(f"{name}.{field}")
    return offenders


def test_free_form_object_detection_recurses_into_arrays_and_nested_dicts() -> None:
    """A free-form leaf must be caught however deep it is nested, not just at the top.

    A single-node test on the immediate schema misses a ``dict[str, Any]`` sitting
    inside a ``list[...]`` (an ``items`` schema) or inside another ``dict[str, ...]``
    (a nested ``additionalProperties`` schema) — both render as a typed OUTER
    container over an untyped inner one, and a future endpoint returning either shape
    would publish it verbatim while :func:`_free_form_properties` swept over an empty
    set. Pinned directly against representative OpenAPI schema fragments rather than
    a real route, since no current endpoint happens to expose either shape.
    """
    free_form_object = {"type": "object", "additionalProperties": True}
    typed_object = {"type": "object", "properties": {"x": {"type": "string"}}}

    assert _is_free_form_object({"type": "array", "items": free_form_object})
    assert not _is_free_form_object({"type": "array", "items": typed_object})
    assert _is_free_form_object(
        {"type": "object", "additionalProperties": free_form_object}
    )
    assert not _is_free_form_object(
        {"type": "object", "additionalProperties": {"type": "string"}}
    )
    # A shapeless object with neither key is NOT flagged (see the function's
    # docstring: this is also how FastAPI's built-in ValidationError.ctx renders).
    assert not _is_free_form_object({"type": "object"})


def test_no_response_schema_discloses_an_unconstrained_free_form_object() -> None:
    """The rule itself: no endpoint may publish an untyped payload it has not declared.

    A new response model carrying a ``dict[str, Any]`` fails HERE, with the property
    named, whether or not its author has ever heard of this ticket. The fix is one of
    two things and never a third: model the payload, or disclose a declared subset of it
    at the boundary the way ``api/v1/runs.py`` does. Widening
    :data:`ALLOWED_FREE_FORM_PROPERTIES` is the third thing, and it is for a payload
    that is not a factory-written artefact at all.
    """
    document = _app().openapi()

    assert _free_form_properties(document) <= ALLOWED_FREE_FORM_PROPERTIES, (
        "a response schema publishes an unmodelled free-form object; see the disclosure "
        "rule in docs/planning/ARCHITECTURE.md, 'Other factory artefacts (read-only)'"
    )


def test_the_runs_artifact_payload_is_not_free_form() -> None:
    """The rule's first subject, pinned as a REGRESSION case and not as the rule.

    Stated separately so the generic sweep above cannot go quietly vacuous: if the walk
    ever stopped reaching ``/runs``'s schemas — a refactor to an undeclared response
    body, a composition keyword the ref-walk misses — the sweep would pass over an empty
    set and this would still fail. The field NAMES are checked in
    ``tests/integration/test_api_runs.py``, over HTTP; what is checked here is only that
    the artefact payload is a constrained shape at all.
    """
    document = _app().openapi()
    schemas = document["components"]["schemas"]

    assert "ProjectedArtifactRead" in _response_schema_names(document), (
        "the /runs artifact schema must be reachable from a response body, or the "
        "sweep above is asserting nothing about it"
    )
    assert not _is_free_form_object(schemas["ProjectedArtifactRead"]["properties"]["data"])


def test_the_untyped_domain_read_type_is_not_on_the_wire_at_all() -> None:
    """T88's ``ArtifactRead`` stays a READING type: untyped below, absent from the contract.

    The rule narrows the disclosure boundary, not the reading layer — the domain keeps
    ``dict[str, Any]`` because no captured real artefact exists to verify a schema
    against. This asserts the two halves of that: the domain type is still untyped, and
    it is not what any endpoint publishes.
    """
    from typing import Any as _Any

    from factory_console.domain.runs import ArtifactRead

    assert ArtifactRead.model_fields["data"].annotation == (dict[str, _Any] | None), (
        "the reading layer stays untyped (T88); T102 changed the wire, not this"
    )
    assert "ArtifactRead" not in _response_schema_names(_app().openapi())
    assert "RunRecord" not in _response_schema_names(_app().openapi())
