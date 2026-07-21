"""Render trusted ticket markdown to sanitized HTML on the server.

The SPA injects this HTML directly (Svelte ``{@html}``), so sanitization must
happen once, here, rather than being re-implemented on the client — one server
surface is the single place XSS defenses live. Markdown is parsed by
markdown-it-py in CommonMark mode with ``html=False`` (raw HTML in the source is
escaped to inert text, never passed through), extended with GFM tables and the
footnote / front-matter plugins. The rendered HTML is then run through a bleach
allowlist as defense-in-depth: only a fixed set of tags, attributes, and URL
protocols survive, so ``<script>``, event handlers, and ``javascript:`` URLs
cannot reach the browser even if a parser edge case let one through.

Both functions are pure and do no I/O.
"""

from __future__ import annotations

import bleach
from markdown_it import MarkdownIt
from mdit_py_plugins.footnote import footnote_plugin
from mdit_py_plugins.front_matter import front_matter_plugin

from factory_console.domain.ticket import Ticket

_MD = (
    MarkdownIt("commonmark", {"html": False, "linkify": True, "typographer": True})
    .enable(["table"])
    .use(footnote_plugin)
    .use(front_matter_plugin)
)
"""Shared CommonMark parser: no raw HTML, with tables, footnotes, front-matter."""

ALLOWED_TAGS = frozenset(
    {
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "p",
        "ul",
        "ol",
        "li",
        "blockquote",
        "pre",
        "code",
        "em",
        "strong",
        "del",
        "sup",
        "sub",
        "a",
        "img",
        "table",
        "thead",
        "tbody",
        "tr",
        "th",
        "td",
        "hr",
        "br",
    }
)
"""Tags allowed to survive sanitization; everything else is stripped."""

ALLOWED_ATTRIBUTES = {"a": ["href", "title"], "img": ["src", "alt", "title"]}
"""Per-tag attribute allowlist — only links and images may carry attributes."""

ALLOWED_PROTOCOLS = ["http", "https", "mailto"]
"""URL schemes permitted on ``href`` / ``src``; blocks ``javascript:`` and friends."""


def render_markdown(md: str) -> str:
    """Render markdown to sanitized, injection-safe HTML.

    Parses ``md`` with the shared ``html=False`` parser, then runs the output
    through the bleach allowlist. ``strip=True`` drops disallowed tags and
    attributes entirely (e.g. the footnote plugin's ``<section class=...>``
    wrapper) rather than escaping them into visible ``&lt;section&gt;`` noise.
    Returns an empty string for empty input.
    """
    return bleach.clean(
        _MD.render(md),
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRIBUTES,
        protocols=ALLOWED_PROTOCOLS,
        strip=True,
    )


def render_ticket_html(ticket: Ticket) -> Ticket:
    """Return a copy of ``ticket`` with ``bodyHtml`` rendered from its markdown.

    :class:`Ticket` is frozen, so this returns a distinct instance via
    ``model_copy`` (the established enrichment pattern) with ``bodyMarkdown``
    left unchanged.
    """
    return ticket.model_copy(update={"bodyHtml": render_markdown(ticket.bodyMarkdown)})
