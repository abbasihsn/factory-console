.PHONY: dev test lint build package smoke release clean

# Override to run off-venv or on a python3-only host, e.g. `make test PYTHON=python3.13`.
PYTHON ?= python

dev:
	./scripts/dev.sh

test:
	$(PYTHON) -m pytest -q && cd frontend && pnpm test

lint:
	ruff check . && ruff format --check . && cd frontend && pnpm lint

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
