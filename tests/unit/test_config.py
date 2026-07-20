"""Tests for ``factory_console.config.Settings`` and its loopback host validator.

Hosts are passed explicitly so the validator runs on the argument rather than
on whatever ``FACTORY_CONSOLE_HOST`` the ambient environment might set.
"""

import pytest
from factory_console.config import Settings
from pydantic import ValidationError


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1"])
def test_loopback_hosts_are_accepted(host: str) -> None:
    """Each loopback host is accepted and preserved on the settings object."""
    settings = Settings(host=host)
    assert settings.host == host


def test_non_loopback_host_raises_validation_error() -> None:
    """A public bind address is rejected with a pydantic ValidationError."""
    with pytest.raises(ValidationError):
        Settings(host="0.0.0.0")
