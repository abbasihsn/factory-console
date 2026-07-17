---
title: Chat message rendering + sanitization
milestone: v1
track: frontend
status: ready
---

# WS-4 · Chat message rendering + sanitization

Render user-authored message bodies as markdown while stripping any active
content. This ticket is the sanitization regression anchor for the whole
console.

## Threat model

User messages are untrusted. A hostile payload such as
`<script>alert(1)</script>` embedded in a message MUST be neutralised before it
reaches the DOM — the console renders ticket bodies server-side through
`bleach`, so this fixture proves the pipeline strips it end-to-end.

## Rendering rules

| Input                          | Rendered as                  |
|--------------------------------|------------------------------|
| `**bold**`                     | `<strong>bold</strong>`      |
| `` `code` ``                   | `<code>code</code>`          |
| `<script>alert(1)</script>`    | escaped / removed, never run |
| `<img onerror=...>`            | attribute stripped           |

## Sanitizer sketch

```html
<!-- A raw payload that must NOT execute after rendering: -->
<script>alert(1)</script>
```

The allow-list is intentionally narrow[^allow]; anything outside it is dropped.

[^allow]: Only structural tags (headings, lists, tables, code, emphasis) and
    safe link attributes survive; event handlers and `<script>` never do.
