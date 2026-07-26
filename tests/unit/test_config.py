"""Unit tests for :mod:`factory_console.config`.

The host validator is the 127.0.0.1 trust-boundary gate; these tests pin its
accept/reject contract. Every case passes ``host=`` explicitly so the outcome is
deterministic and independent of any ambient ``FACTORY_CONSOLE_*`` environment.
The ``write_token`` cases pin the v2 write-secret override: unset means "let
``create_app`` mint one", ``FACTORY_CONSOLE_WRITE_TOKEN`` pins it instead, and a pin
that is set but unusable (blank or too short) is rejected rather than quietly
replaced by a generated token. :func:`read_write_token` is covered alongside them
because it is the accessor both boot paths actually use, and its whole reason to
exist is reading that ONE variable without validating the others.
"""

import pytest
from pydantic import ValidationError

from factory_console.config import (
    MIN_WRITE_TOKEN_CHARS,
    WRITE_TOKEN_HEADER,
    Settings,
    read_write_token,
)

# Every ``FACTORY_CONSOLE_*`` variable ``Settings`` reads, cleared via monkeypatch so
# an ambient value in the developer's shell can never flip a default assertion.
ENV_VARS = (
    "FACTORY_CONSOLE_HOST",
    "FACTORY_CONSOLE_PORT",
    "FACTORY_CONSOLE_LOG_LEVEL",
    "FACTORY_CONSOLE_WRITE_TOKEN",
)

# A pin long enough to satisfy the minimum, used wherever a valid override is needed.
VALID_PIN = "pinned-write-token-for-tests"


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1"])
def test_loopback_hosts_are_accepted(host: str) -> None:
    assert Settings(host=host).host == host


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.10"])
def test_non_loopback_host_is_rejected(host: str) -> None:
    with pytest.raises(ValidationError):
        Settings(host=host)


def test_defaults_are_loopback(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    settings = Settings()
    assert settings.host == "127.0.0.1"
    assert settings.port == 0
    assert settings.log_level == "INFO"
    # None, not a baked-in secret: an unpinned token is minted fresh at every boot.
    assert settings.write_token is None


def test_write_token_is_read_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("FACTORY_CONSOLE_WRITE_TOKEN", VALID_PIN)
    assert Settings().write_token == VALID_PIN


@pytest.mark.parametrize(
    "pin",
    ["", "   ", "short", "x" * (MIN_WRITE_TOKEN_CHARS - 1)],
    ids=["blank", "whitespace", "short", "one-char-under"],
)
def test_unusable_write_token_pin_is_rejected(pin: str) -> None:
    # The blank case is the dangerous one: an empty pin is falsy, so without this
    # validator ``write_token or secrets.token_urlsafe(...)`` would mint a RANDOM
    # token, silently discarding the operator's override and 401-ing every write with
    # nothing to diagnose it by. Fail fast at config time instead.
    with pytest.raises(ValidationError):
        Settings(write_token=pin)


def test_write_token_pin_is_stripped() -> None:
    # A pin pasted with stray whitespace must not become a different secret than the
    # one the operator thinks they set.
    assert Settings(write_token=f"  {VALID_PIN}  ").write_token == VALID_PIN


def test_read_write_token_returns_none_when_unpinned(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    # None is create_app's cue to mint a fresh per-session token.
    assert read_write_token() is None


def test_read_write_token_returns_the_pin(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("FACTORY_CONSOLE_WRITE_TOKEN", VALID_PIN)
    assert read_write_token() == VALID_PIN


def test_read_write_token_ignores_the_other_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    # The reason this helper exists: reading the token must NOT validate host/port/
    # log_level. A bare ``Settings()`` here would raise on the non-loopback host and
    # kill a boot that never consulted the env var for its host at all (the CLI's
    # --host beats it, and create_dev_app lets dev.sh pass the host to uvicorn).
    for var in ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("FACTORY_CONSOLE_HOST", "0.0.0.0")
    monkeypatch.setenv("FACTORY_CONSOLE_LOG_LEVEL", "not-a-level")
    monkeypatch.setenv("FACTORY_CONSOLE_WRITE_TOKEN", VALID_PIN)
    assert read_write_token() == VALID_PIN


def test_read_write_token_raises_value_error_on_a_bad_pin(monkeypatch: pytest.MonkeyPatch) -> None:
    # A plain ValueError, not pydantic's ValidationError, so the CLI reports it via the
    # same exit-2 path as a bad host or log level. Asserted on the exact type because
    # ValidationError SUBCLASSES ValueError — a bare ``pytest.raises(ValueError)`` here
    # would pass even if the raw pydantic error leaked through unconverted.
    for var in ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("FACTORY_CONSOLE_WRITE_TOKEN", "")
    with pytest.raises(ValueError, match="FACTORY_CONSOLE_WRITE_TOKEN") as excinfo:
        read_write_token()
    assert type(excinfo.value) is ValueError
    # The operator-facing message must state the way out, not just the rejection.
    assert str(MIN_WRITE_TOKEN_CHARS) in str(excinfo.value)


def test_write_token_header_is_the_documented_name() -> None:
    # The one home of the header name — the app factory's stderr announcement, the
    # verifier dependency, and the OpenAPI security scheme all read it from here.
    assert WRITE_TOKEN_HEADER == "X-Factory-Write-Token"
