.PHONY: dev test lint codegen build package smoke release clean

# Override to run off-venv or on a python3-only host, e.g. `make test PYTHON=python3.13`.
PYTHON ?= python

dev:
	./scripts/dev.sh

test:
	$(PYTHON) -m pytest -q && cd frontend && pnpm test

# Regenerate frontend/src/lib/api/types.ts from THIS working tree's models.
#
# The schema is built in-process (scripts/openapi_schema.py) rather than fetched from a
# running server, and that is a correctness fix, not a convenience one. The old default
# fetched http://127.0.0.1:8000/api/v1/openapi.json — whatever process was listening on
# that port. A dev server left running from an earlier checkout answers happily,
# openapi-typescript reports success, and types.ts is regenerated against models that no
# longer exist. Silent, and it bit exactly that way: a newly added response field was
# simply absent from a codegen that had just succeeded.
#
# PYTHONPATH mirrors pyproject's `pythonpath = ["server", "tests"]` — this repo is
# usually run un-installed, and pytest is the only other thing that needs the same seam.
# The intermediate schema is written under frontend/ so pnpm resolves it relative to its
# own cwd; it is gitignored, and regenerated every run rather than reused.
codegen:
	PYTHONPATH=server $(PYTHON) scripts/openapi_schema.py > frontend/.openapi.json
	cd frontend && pnpm codegen

# lint delegates to pre-commit instead of enumerating checks, because CI runs
# `pre-commit run --all-files` and a Makefile that re-derives the hook set is a
# second authority that drifts from the first (it did, in both directions: it
# walked into tests/fixtures/ that the hooks exclude, and it never ran prettier
# that the hooks do). Add new checks to .pre-commit-config.yaml and they land
# here and in CI at once. No fallback when pre-commit is missing — a fallback
# that checks something different is the defect this delegation removes.
#
# Unlike the recipe it replaces, this one WRITES: ruff-check runs with --fix and
# the whitespace/EOF hooks rewrite in place, so a failing run may have already
# corrected what it reported. Review `git diff` and re-run to confirm clean.
lint:
	@command -v pre-commit >/dev/null 2>&1 || { \
		echo "make lint requires pre-commit, which is not on PATH." >&2; \
		echo "Install the dev extra and the hooks:" >&2; \
		echo "  pip install -e '.[dev]'" >&2; \
		echo "  pre-commit install" >&2; \
		exit 1; \
	}
	pre-commit run --all-files

# build and package both go through scripts/package.sh — the one reproducible
# recipe that rebuilds the SPA and bakes it into the (gitignored) _static/ before
# building the wheel. A bare `python -m build --wheel` would ship whatever stale or
# empty _static/ happens to be on disk, so `make build`/`make smoke` could silently
# produce and green-light a SPA-less wheel.
build package:
	PYTHON=$(PYTHON) ./scripts/package.sh

smoke: package
	PYTHON=$(PYTHON) ./scripts/smoke.sh

release:
	@echo "Release: push a v* tag (e.g. 'git tag v0.1.0 && git push origin v0.1.0'); CI builds + publishes."

clean:
	rm -rf build dist *.egg-info server/factory_console/_static frontend/build frontend/.svelte-kit
