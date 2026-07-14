# [T14] Server-side markdown renderer (markdown_render.py + sanitization)

milestone: MVP · track: file-adapter · depends_on: T07 · provides: `render_markdown(md)`; `render_ticket_html(ticket)` — server-rendered sanitized HTML the SPA can inject via `{@html}`

## Context

SPA injects rendered HTML from the server so sanitization has one surface. markdown-it-py + mdit-py-plugins (tables, footnotes, front-matter) then bleach allowlist so `<script>`, event handlers, `javascript:` URLs cannot slip through.

## Staged approach

1. `file_adapter/markdown_render.py`.
2. Confirm `bleach` is in pyproject deps (T02 lists it — verify).
3. Module-level `MarkdownIt('commonmark', {'html': False, 'linkify': True, 'typographer': True}).enable(['table']).use(footnote_plugin).use(front_matter_plugin)`.
4. `render_markdown(md: str) -> str`: parse -> render -> sanitize via bleach with allowed tags (`h1-h6, p, ul, ol, li, blockquote, pre, code, em, strong, a[href title], img[src alt title], table/thead/tbody/tr/th/td, hr, br, sup, sub, del`) and allowed protocols (`http, https, mailto`).
5. `render_ticket_html(ticket) -> Ticket`: returns `ticket.model_copy(update={'bodyHtml': render_markdown(ticket.bodyMarkdown)})`.
6. `tests/unit/test_markdown_render.py`: paragraph; heading + list; table; footnote; `<script>` stripped; `javascript:` href sanitized; `onerror` handler stripped; empty -> empty.

## Critical files

- `server/factory_console/file_adapter/markdown_render.py`
- `tests/unit/test_markdown_render.py`
- `pyproject.toml`

## Interface & data

Pure functions, no I/O. NFR: XSS-sanitization (allowlist); `html=False` in md parser.

## Verification

`pytest tests/unit/test_markdown_render.py -q` green including all sanitization cases; ruff clean.
