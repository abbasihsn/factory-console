# [T26] Release workflow (tag vX.Y.Z -> PyPI via OIDC + GitHub Release)

milestone: MVP · track: foundation · depends_on: T18 · provides: GitHub Actions `release.yml` that on a pushed `vX.Y.Z` tag builds the wheel (SPA baked in), publishes to PyPI via trusted publishing, and creates a GitHub Release with wheel + sdist

## Context

Closes the loop: `uvx factory-console` implies a wheel on PyPI. Uses PyPI trusted publishing (OIDC `id-token`) so no `PYPI_API_TOKEN` secret. A small guard step enforces `tag` matches `__version__`.

## Staged approach

1. `.github/workflows/release.yml`. `name: Release`. `on: {push: {tags: ['v*.*.*']}}`.
   - `jobs.build`: `runs-on: ubuntu-latest`. Steps: checkout `fetch-depth 0`; `setup-python 3.12`; `setup-node 20 + pnpm`; assert tag matches `__version__` (`python -c 'import factory_console, sys; assert factory_console.__version__ == sys.argv[1]' "${GITHUB_REF_NAME#v}"`); `pnpm --dir frontend install --frozen-lockfile`; `bash scripts/package.sh`; upload `dist/` as artifact.
   - `jobs.publish`: `needs: build`; `runs-on: ubuntu-latest`; `environment: pypi`; `permissions: id-token: write, contents: write`. Steps: download `dist` artifact; `pypa/gh-action-pypi-publish@release/v1` (OIDC); `softprops/action-gh-release@v2` with `files: dist/*`, `generate_release_notes: true`.
2. Document `git tag vX.Y.Z && git push origin vX.Y.Z` in `docs/contributing.md`.

## Critical files

- `.github/workflows/release.yml`

## Interface & data

N/A — infra. Produces the PyPI artifact consumed via `uvx/pipx`; artifact shape enforced by `scripts/package.sh`.

## Verification

On a `v0.0.1a1` tag against a TestPyPI-configured environment: build produces wheel; publish uploads to TestPyPI; GitHub Release appears. Guard failure on tag/version mismatch.
