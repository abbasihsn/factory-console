.PHONY: dev test lint build package smoke release clean

# Override to run off-venv or on a python3-only host, e.g. `make test PYTHON=python3.13`.
PYTHON ?= python

dev:
	./scripts/dev.sh

test:
	$(PYTHON) -m pytest -q && cd frontend && pnpm test

# lint delegates to pre-commit instead of enumerating checks, because CI runs
# `pre-commit run --all-files` and a Makefile that re-derives the hook set is a
# second authority that drifts from the first (it did, in both directions: it
# walked into tests/fixtures/ that the hooks exclude, and it never ran prettier
# that the hooks do). Add new checks to .pre-commit-config.yaml and they land
# here and in CI at once. No fallback when pre-commit is missing — a fallback
# that checks something different is the defect this delegation removes.
lint:
	@command -v pre-commit >/dev/null 2>&1 || { \
		echo "make lint requires pre-commit, which is not on PATH."; \
		echo "Install the dev extra and the hooks:"; \
		echo "  pip install -e '.[dev]'"; \
		echo "  pre-commit install"; \
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
