# [T02] Python package skeleton + pyproject.toml + factory-console entry point

milestone: MVP · track: foundation · depends_on: T01 · provides: Installable factory_console Python package with pinned deps and factory-console console-script entry point declared

## Context

Makes the Python side installable (`pip install -e .[dev]`) so downstream Python tracks can add modules and be imported. Declares runtime deps per `ARCHITECTURE.md` tech_stack (FastAPI, Uvicorn[standard], Typer, Pydantic v2, pydantic-settings, markdown-it-py, mdit-py-plugins, bleach, PyYAML), dev deps (pytest, pytest-asyncio, httpx, ruff, build, pre-commit), and `factory-console = 'factory_console.cli:app'` console-script entry (target is added in T06). Sets the package layout via `[tool.setuptools.packages.find] where=['server']`.

## Staged approach

1. Create `server/factory_console/__init__.py` containing only `__version__ = '0.1.0'`.
2. Write `pyproject.toml`:
   - `[build-system]` requires=`['setuptools>=68','wheel']`.
   - `[project]` name=`'factory-console'`, version dynamic-from `__init__`, `python-requires='>=3.11'`, dependencies listed above.
   - `[project.optional-dependencies].dev` listed above.
   - `[project.scripts]` `factory-console='factory_console.cli:app'`.
   - `[tool.setuptools.packages.find]` `where=['server']` `include=['factory_console*']`.
   - `[tool.setuptools.package-data]` `factory_console=['_static/**/*']`.
   - `[tool.ruff]` `line-length=100` `target-version='py311'`; `[tool.ruff.lint].select=['E','F','W','I','B','UP','SIM']`.
   - `[tool.pytest.ini_options]` `testpaths=['tests']` `asyncio_mode='auto'`.

## Critical files

- `pyproject.toml`
- `server/factory_console/__init__.py`

## Interface & data

Consumes CLI contract (see `ARCHITECTURE.md` "CLI contract"): declares `factory-console` entry point bound to `factory_console.cli:app`. Consumes packaging contract: `[tool.setuptools.package-data]` guarantees `_static/` ships in the wheel.

## Verification

`pip install -e .[dev]` succeeds; `python -c 'import factory_console; print(factory_console.__version__)'` prints `0.1.0`; `factory-console --help` errors cleanly (`cli:app` not yet defined — T06 lands it); `ruff check .` clean.
