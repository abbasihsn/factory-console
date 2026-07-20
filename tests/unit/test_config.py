"""Unit tests for :mod:`factory_console.config`.

The host validator is the 127.0.0.1 trust-boundary gate; these tests pin its
accept/reject contract. Every case passes ``host=`` explicitly so the outcome is
deterministic and independent of any ambient ``FACTORY_CONSOLE_*`` environment.
"""

import pytest
from pydantic import ValidationError

from factory_console.config import Settings


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1"])
def test_loopback_hosts_are_accepted(host: str) -> None:
    assert Settings(host=host).host == host


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.10"])
def test_non_loopback_host_is_rejected(host: str) -> None:
    with pytest.raises(ValidationError):
        Settings(host=host)


def test_defaults_are_loopback(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("FACTORY_CONSOLE_HOST", "FACTORY_CONSOLE_PORT", "FACTORY_CONSOLE_LOG_LEVEL"):
        monkeypatch.delenv(var, raising=False)
    settings = Settings()
    assert settings.host == "127.0.0.1"
    assert settings.port == 0
    assert settings.log_level == "INFO"
