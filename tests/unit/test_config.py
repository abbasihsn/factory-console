"""Unit tests for :mod:`factory_console.config`.

The host validator is the 127.0.0.1 trust-boundary gate; these tests pin its
accept/reject contract. Every case passes ``host=`` explicitly so the outcome is
deterministic and independent of any ambient ``FACTORY_CONSOLE_*`` environment.
The ``write_token`` cases pin the v2 write-secret override: unset means "let
``create_app`` mint one", and ``FACTORY_CONSOLE_WRITE_TOKEN`` pins it instead.
"""

import pytest
from pydantic import ValidationError

from factory_console.config import WRITE_TOKEN_HEADER, Settings

# Every ``FACTORY_CONSOLE_*`` variable ``Settings`` reads, cleared via monkeypatch so
# an ambient value in the developer's shell can never flip a default assertion.
ENV_VARS = (
    "FACTORY_CONSOLE_HOST",
    "FACTORY_CONSOLE_PORT",
    "FACTORY_CONSOLE_LOG_LEVEL",
    "FACTORY_CONSOLE_WRITE_TOKEN",
)


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
    monkeypatch.setenv("FACTORY_CONSOLE_WRITE_TOKEN", "pinned-token")
    assert Settings().write_token == "pinned-token"


def test_write_token_header_is_the_documented_name() -> None:
    # The one home of the header name — the app factory's stderr announcement, the
    # verifier dependency, and the OpenAPI security scheme all read it from here.
    assert WRITE_TOKEN_HEADER == "X-Factory-Write-Token"
