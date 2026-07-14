# [T04] Observability skeleton (logging.py + errors.py base + config.py)

milestone: MVP · track: foundation · depends_on: T02 · provides: Shared logging formatter, FactoryConsoleError base + to_error_response helper, pydantic-settings Settings pinned to 127.0.0.1

## Context

Cross-cutting substrate every backend endpoint and file-adapter module imports. `logging.py` exposes `configure_logging(level)` + `request_log_line(method, path, status, dur_ms)`; `errors.py` defines ONLY the `FactoryConsoleError` base + `to_error_response` helper (per synthesis note: concrete subclasses live in the modules that raise them); `config.py` defines `Settings(host, port, log_level)` with a `field_validator('host')` rejecting anything not in `{127.0.0.1, localhost, ::1}`.

## Staged approach

1. `server/factory_console/logging.py`: `configure_logging(level: str)` sets root logger, adds `StreamHandler(sys.stderr)` with `'%(levelname)s %(asctime)s %(message)s'`; `request_log_line(method, path, status, dur_ms)` returns a formatted string.
2. `server/factory_console/errors.py`: `class FactoryConsoleError(Exception)` with `code: str, message: str, status: int, details: object | None`; `def to_error_response(exc: FactoryConsoleError) -> dict` returning `{'error': {'code': exc.code, 'message': exc.message, **({'details': exc.details} if exc.details is not None else {})}}`. Include a module-header comment: "Concrete subclasses live in the modules that raise them (`file_adapter/*`, `services/*`), keeping the exception owner and the raiser co-located."
3. `server/factory_console/config.py`: `class Settings(BaseSettings)`: `host: str = '127.0.0.1'`; `port: int = 0`; `log_level: str = 'INFO'`; `model_config = SettingsConfigDict(env_prefix='FACTORY_CONSOLE_')`; `@field_validator('host')` rejects non-loopback.
4. `tests/unit/test_config.py`: valid loopback hosts accepted; `'0.0.0.0'` raises `ValidationError`. `tests/unit/test_errors.py`: `to_error_response` omits `'details'` when `None`; includes it when set.

## Critical files

- `server/factory_console/logging.py`
- `server/factory_console/errors.py`
- `server/factory_console/config.py`
- `tests/unit/test_config.py`
- `tests/unit/test_errors.py`

## Interface & data

Implements REST v1 error envelope `{ error: { code, message, details? } }` via `to_error_response`. Config surfaces `FACTORY_CONSOLE_HOST/PORT/LOG_LEVEL` env vars. Host validator enforces the 127.0.0.1 NFR (auth-N/A trust boundary). NFR flags: security (host binding pin), observability (log-line format).

## Verification

`pytest tests/unit/test_config.py tests/unit/test_errors.py -q` green; `python -c 'from factory_console.config import Settings; Settings(host="0.0.0.0")'` raises `ValidationError`.
