# Contributing

Local dev setup lives in the [Development](../README.md#development) section of the README. This doc covers cutting a release.

## Releasing

Releases are tag-driven. Pushing a `vX.Y.Z` tag triggers [`.github/workflows/release.yml`](../.github/workflows/release.yml), which builds the wheel (with the SPA baked into `_static/`) plus the sdist, publishes to PyPI, and creates a GitHub Release with both artifacts attached.

To cut a release:

1. Bump `__version__` in [`server/factory_console/__init__.py`](../server/factory_console/__init__.py).
2. Commit it on `main`.
3. Tag and push:

   ```
   git tag vX.Y.Z
   git push origin vX.Y.Z
   ```

The tag **minus the leading `v`** must equal `__version__` — e.g. tag `v0.1.0` when `__version__ = "0.1.0"`. The workflow's guard step asserts this and fails fast on a mismatch, so a mistagged release never reaches PyPI.

### Trusted publishing

Publishing uses PyPI **trusted publishing (OIDC)** through the `pypi` GitHub Environment: the workflow mints a short-lived OIDC token per run, so there is **no `PYPI_API_TOKEN` secret** to store or rotate. Point the `pypi` environment at TestPyPI to rehearse a release without touching production PyPI.
