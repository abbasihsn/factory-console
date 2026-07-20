"""Application settings sourced from ``FACTORY_CONSOLE_*`` env vars.

The host is validator-pinned to loopback so the console never binds a public
interface (the 127.0.0.1 trust-boundary NFR).
"""

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


class Settings(BaseSettings):
    """Runtime configuration, loaded from the environment with a loopback-only host."""

    host: str = "127.0.0.1"
    port: int = 0
    log_level: str = "INFO"

    model_config = SettingsConfigDict(env_prefix="FACTORY_CONSOLE_")

    @field_validator("host")
    @classmethod
    def _host_must_be_loopback(cls, value: str) -> str:
        """Reject any host outside the loopback set; pydantic wraps this as ValidationError."""
        if value not in _LOOPBACK_HOSTS:
            raise ValueError(
                f"host must be loopback (one of {sorted(_LOOPBACK_HOSTS)}), got {value!r}"
            )
        return value
