"""Unit tests for :mod:`factory_console.file_adapter.markdown_render`.

Cover the markdown feature surface (paragraph, heading + list, GFM table,
footnote) and the XSS allowlist: raw ``<script>`` and event handlers are escaped
to inert text by ``html=False`` (no live tag survives), a ``javascript:`` link is
refused rather than emitted with the scheme, and empty input renders empty.
``render_ticket_html`` is checked for the frozen-copy semantics it inherits from
``model_copy``. All cases are pure and hermetic — no I/O.
"""

from pathlib import Path

import pytest
from pydantic import ValidationError

from factory_console.domain import Ticket
from factory_console.file_adapter.markdown_render import (
    render_markdown,
    render_ticket_html,
)

# --------------------------------------------------------------------------- #
# Markdown feature surface
# --------------------------------------------------------------------------- #


def test_paragraph_renders_p_tag() -> None:
    result = render_markdown("Hello world")

    assert "<p>Hello world</p>" in result


def test_heading_and_list_render_their_tags() -> None:
    result = render_markdown("# Title\n\n- one\n- two")

    assert "<h1>Title</h1>" in result
    assert "<li>one</li>" in result


def test_gfm_table_renders_table_and_cells() -> None:
    result = render_markdown("| a | b |\n| - | - |\n| 1 | 2 |")

    assert "<table>" in result
    assert "<td>1</td>" in result


def test_footnote_content_survives_sanitization() -> None:
    # The plugin's <section class=...> wrapper is stripped by bleach; the
    # footnote reference (<sup>) and the note text must still come through.
    result = render_markdown("text[^1]\n\n[^1]: note")

    assert "<sup>" in result
    assert "note" in result


# --------------------------------------------------------------------------- #
# XSS allowlist
# --------------------------------------------------------------------------- #


def test_script_tag_is_not_a_live_tag() -> None:
    # html=False escapes it to inert text (&lt;script&gt;); no live tag survives.
    result = render_markdown("<script>alert(1)</script>")

    assert "<script" not in result


def test_javascript_href_scheme_is_never_emitted() -> None:
    # markdown-it refuses to build the link and renders it as literal text, so no
    # anchor carries the javascript: scheme.
    result = render_markdown("[click](javascript:alert(1))")

    assert 'href="javascript:' not in result.lower()


def test_onerror_img_is_not_a_live_element() -> None:
    # html=False escapes the raw <img> to inert text; no live img survives.
    result = render_markdown('<img src=x onerror="alert(1)">')

    assert "<img" not in result.lower()


def test_allowed_image_renders_with_src() -> None:
    result = render_markdown("![a](http://e.com/i.png)")

    assert "<img" in result
    assert 'src="http://e.com/i.png"' in result


def test_empty_input_renders_empty() -> None:
    assert render_markdown("") == ""


# --------------------------------------------------------------------------- #
# render_ticket_html
# --------------------------------------------------------------------------- #


def _make_ticket(body_markdown: str) -> Ticket:
    """A minimal ticket carrying ``body_markdown`` and an empty ``bodyHtml``."""
    return Ticket(
        id="T14",
        title="Markdown renderer",
        status="todo",
        track="file-adapter",
        milestone="MVP",
        filePath=Path("docs/planning/tickets/mvp/T14.md"),
        bodyMarkdown=body_markdown,
        bodyHtml="",
        raw={"id": "T14"},
    )


def test_render_ticket_html_sets_body_html_and_keeps_a_frozen_copy() -> None:
    ticket = _make_ticket("# Hi\n\ntext")

    rendered = render_ticket_html(ticket)

    assert rendered.bodyHtml == render_markdown("# Hi\n\ntext")
    # A distinct instance; the source markdown is untouched.
    assert rendered is not ticket
    assert rendered.bodyMarkdown == ticket.bodyMarkdown
    assert ticket.bodyHtml == ""
    # Still frozen.
    with pytest.raises(ValidationError):
        rendered.bodyHtml = "mutated"
