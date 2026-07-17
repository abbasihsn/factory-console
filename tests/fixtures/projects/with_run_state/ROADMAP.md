# Snip — Roadmap

A self-hosted URL shortener: mint a short code, redirect on lookup, count the clicks.

## MVP — mint and store

The smallest thing that shortens a link and serves it back.

- **S01 — Generate collision-resistant short codes** · backend · planned
- **S02 — Persist link mappings with a hit counter** · data · planned
- **S05 — Bootstrap the FastAPI application and health probe** · backend · done

## v1 — serve and create

Make it usable from a browser, end to end.

- **S03 — Redirect endpoint with click accounting** · backend · in-progress
- **S04 — Public shorten form and result page** · frontend · in-review

## v2 — insight

Show owners what their links are doing.

- **S06 — Per-link click analytics dashboard** · frontend · planned
