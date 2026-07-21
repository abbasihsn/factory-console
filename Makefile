.PHONY: dev test lint build package smoke release clean

# Override to run off-venv or on a python3-only host, e.g. `make test PYTHON=python3.13`.
PYTHON ?= python

dev:
	./scripts/dev.sh

test:
	$(PYTHON) -m pytest -q && cd frontend && pnpm test

lint:
	ruff check . && ruff format --check . && cd frontend && pnpm lint

build:
	$(PYTHON) -m build --wheel

package:
	PYTHON=$(PYTHON) ./scripts/package.sh

smoke: build
	PYTHON=$(PYTHON) ./scripts/smoke.sh

release:
	@echo "Release: push a v* tag (e.g. 'git tag v0.1.0 && git push origin v0.1.0'); CI builds + publishes."

clean:
	rm -rf build dist *.egg-info server/factory_console/_static frontend/build frontend/.svelte-kit
