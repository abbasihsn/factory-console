# [T19] Docs skeleton (architecture.md + usage.md + contributing.md) + README quickstart

milestone: MVP · track: foundation · depends_on: T01, T09 · provides: `docs/{architecture, usage, contributing}.md` stubs and a proper README replacing the day-one stub

## Context

Each doc starts as a stub other tracks fill in as they land features. `contributing.md` is the only page with real content on day one because contributors need the dev loop (`make dev`, `make test`, `pre-commit install`, release process) before any code exists.

## Staged approach

1. `docs/architecture.md`: one-paragraph description of layered CLI -> HTTP -> Domain -> FileAdapter architecture (paraphrase `VISION.md` + `ARCHITECTURE.md`); link to `docs/planning/ARCHITECTURE.md`; stub Contracts section listing the four contract names.
2. `docs/usage.md`: Install (`uvx factory-console` / `pipx install factory-console`); Run (`cd my-factory-project && factory-console`); Flags section listing the CLI contract flags; Exit codes `0/1/2/3`; "What you'll see" TODO for T34 screenshots.
3. `docs/contributing.md`: dev loop (`git clone`, `pip install -e .[dev]`, `pnpm --dir frontend install`, `pre-commit install`, `make dev`); test loop (`make test`, `make lint`); packaging (`make package`, `make smoke`); release (`git tag vX.Y.Z && git push origin vX.Y.Z` triggers `release.yml`); track boundaries table copied from `ARCHITECTURE.md` tracks.
4. Rewrite `README.md`: one-line pitch, CI badge, Install, 60-second usage example, link to `docs/*.md`.

## Critical files

- `docs/architecture.md`
- `docs/usage.md`
- `docs/contributing.md`
- `README.md`

## Interface & data

References CLI + REST v1 contracts by name; does not redefine.

## Verification

`grep -l 'factory-console' docs/*.md README.md` finds all four; prettier clean; README links to `docs/*.md` resolve.
