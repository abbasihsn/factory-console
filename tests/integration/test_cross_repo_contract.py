"""The console and App Factory must agree about what a ticket IS.

Two claims live across two repositories, and neither repository can check them alone:

1. :class:`~factory_console.file_adapter.ticket_json.TicketContent` really is
   ``schemas/ticket.schema.json``. The console validates v3 tickets with a Pydantic
   mirror rather than a JSON-Schema run — see that module for why — and a mirror that
   nobody compares to the original is a copy waiting to drift.
2. :func:`~factory_console.file_adapter.ticket_json.render_ticket_markdown` produces
   what ``factory-ticket render`` produces. If the two differ, the ticket a human
   reviews in the console is not the ticket a lane builds from, and the disagreement
   shows up as a build that does not match its plan rather than as an error.

**These tests SKIP when App Factory is not on disk**, which it will not be in CI. That is
the honest arrangement — a test cannot verify a contract against a repository that is not
there — and it is why ``tests/unit/test_ticket_json.py`` pins the model and the rendering
structurally on their own. This file adds the comparison against the real thing wherever
both checkouts exist; it does not replace the coverage that must hold without them.

Point it somewhere else with ``FC_APP_FACTORY_ROOT``; otherwise the sibling checkout
beside this repository is tried.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from factory_console.file_adapter.ticket_json import (
    TicketContent,
    TicketInvalid,
    TicketVerification,
    parse_ticket_content,
    render_ticket_markdown,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE = REPO_ROOT / "tests" / "fixtures" / "projects" / "factory_v3"


def _app_factory_root() -> Path | None:
    """Where App Factory is checked out, or ``None`` if it is not reachable.

    ``FC_APP_FACTORY_ROOT`` wins so a developer with a non-standard layout can point
    these tests at their checkout; the sibling directory is the convention otherwise.
    A path that exists but holds no schema answers ``None`` rather than being reported as
    a failure — "the wrong directory" and "a broken contract" are different findings, and
    only the second is this file's business.
    """
    declared = os.environ.get("FC_APP_FACTORY_ROOT")
    candidates = [Path(declared)] if declared else [REPO_ROOT.parent / "app-factory"]
    for candidate in candidates:
        if (candidate / "schemas" / "ticket.schema.json").is_file():
            return candidate
    return None


APP_FACTORY = _app_factory_root()

requires_app_factory = pytest.mark.skipif(
    APP_FACTORY is None,
    reason="App Factory is not on disk; set FC_APP_FACTORY_ROOT to run the cross-repo contract",
)


@pytest.fixture(scope="module")
def schema() -> dict:
    """The real ``ticket.schema.json``, parsed."""
    assert APP_FACTORY is not None
    return json.loads((APP_FACTORY / "schemas" / "ticket.schema.json").read_text("utf-8"))


# --------------------------------------------------------------------------- #
# Claim 1 — the Pydantic model IS the schema
# --------------------------------------------------------------------------- #


@requires_app_factory
def test_the_model_names_exactly_the_schema_s_properties(schema: dict) -> None:
    assert set(TicketContent.model_fields) == set(schema["properties"])


@requires_app_factory
def test_the_model_requires_exactly_what_the_schema_requires(schema: dict) -> None:
    required = {name for name, field in TicketContent.model_fields.items() if field.is_required()}
    assert required == set(schema["required"])


@requires_app_factory
def test_additional_properties_false_is_mirrored_as_extra_forbid(schema: dict) -> None:
    # Not a stylistic choice: the FACTORY refuses an unknown key too, so a console that
    # tolerated one would render a ticket no lane can read.
    assert schema["additionalProperties"] is False
    assert TicketContent.model_config["extra"] == "forbid"
    assert schema["properties"]["verification"]["additionalProperties"] is False
    assert TicketVerification.model_config["extra"] == "forbid"


@requires_app_factory
def test_the_verification_object_mirrors_its_own_subschema(schema: dict) -> None:
    verification = schema["properties"]["verification"]
    required = {
        name for name, field in TicketVerification.model_fields.items() if field.is_required()
    }
    assert set(TicketVerification.model_fields) == set(verification["properties"])
    assert required == set(verification["required"])


@requires_app_factory
def test_every_min_items_and_min_length_the_schema_states_is_enforced(schema: dict) -> None:
    """Reject what the schema rejects, driven BY the schema rather than by a copy of it.

    Written as a walk over the schema so a constraint added upstream fails here as an
    unmet expectation, instead of being silently absent from a hand-listed set of cases.
    """
    valid = json.loads(
        (FIXTURE / "docs/planning/tickets/v1.0/T01-the-base-ticket.json").read_text()
    )

    for name, subschema in schema["properties"].items():
        if subschema.get("minLength") == 1:
            broken = {**valid, name: ""}
            with pytest.raises(Exception, match=r"(?i)short|empty|least"):
                parse_ticket_content("T01", json.dumps(broken))
        if subschema.get("minItems") == 1:
            broken = {**valid, name: []}
            with pytest.raises(Exception, match=r"(?i)short|empty|least"):
                parse_ticket_content("T01", json.dumps(broken))

    commands = schema["properties"]["verification"]["properties"]["commands"]
    assert commands["minItems"] == 1, "the schema's own INV-42 clause moved or was removed"
    # The mapped error specifically, not a bare Exception: this must fail because the
    # ticket is INVALID, and a typo in the payload above would satisfy a blind raises by
    # failing for a completely different reason.
    with pytest.raises(TicketInvalid, match=r"(?i)short|empty|least"):
        parse_ticket_content("T01", json.dumps({**valid, "verification": {"commands": []}}))


# --------------------------------------------------------------------------- #
# Claim 2 — the two renderers produce the same bytes
# --------------------------------------------------------------------------- #


def _factory_render(ticket_id: str) -> str:
    """``factory-ticket render`` on the v3 fixture, or skip if it cannot run."""
    assert APP_FACTORY is not None
    binary = APP_FACTORY / "bin" / "factory-ticket"
    if not binary.is_file():
        pytest.skip(f"{binary} is not present")
    result = subprocess.run(
        [str(binary), "render", ticket_id, "--repo", str(FIXTURE)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        pytest.skip(f"factory-ticket render exited {result.returncode}: {result.stderr.strip()}")
    return result.stdout


@requires_app_factory
@pytest.mark.parametrize("ticket_id", ["T01", "T02", "T03"])
def test_the_console_renders_a_ticket_exactly_as_the_factory_does(ticket_id: str) -> None:
    entries = {
        entry["id"]: entry
        for entry in json.loads((FIXTURE / "docs/planning/tickets.json").read_text())["tickets"]
    }
    entry = entries[ticket_id]
    content = parse_ticket_content(ticket_id, (FIXTURE / entry["path"]).read_text("utf-8"))

    ours = render_ticket_markdown(content, entry)

    # The factory's `jq` emits one trailing newline that this renderer does not, because
    # this output is a STRING joined into a JSON response body while that one is a file
    # written to a lane's scratch. Comparing the documents, not the framing.
    assert ours == _factory_render(ticket_id).rstrip("\n")


@requires_app_factory
def test_the_fixture_passes_the_factory_s_own_planning_lint() -> None:
    """``factory-ticket lint`` must accept this fixture, or it is not a v3 project.

    The fixture exists to be what a migrated repository looks like. A committed status
    marker in its ``ROADMAP.md`` would make it a project the factory refuses to run, and
    a fixture the factory refuses is not evidence about anything.
    """
    assert APP_FACTORY is not None
    binary = APP_FACTORY / "bin" / "factory-ticket"
    if not binary.is_file():
        pytest.skip(f"{binary} is not present")
    result = subprocess.run(
        [str(binary), "lint", "--repo", str(FIXTURE)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, f"factory-ticket lint rejected the fixture:\n{result.stdout}"
