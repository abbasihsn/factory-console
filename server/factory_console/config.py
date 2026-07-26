"""Application settings, pinned to the loopback trust boundary.

``Settings`` sources ``FACTORY_CONSOLE_HOST/PORT/LOG_LEVEL/WRITE_TOKEN`` from the
environment and validates ``host`` against the loopback allow-set at startup. This
enforces the 127.0.0.1 trust-boundary NFR: the console only ever binds to the local
machine, so it never becomes reachable from the network, which is exactly why the
boundary must hold.

Reads are authenticated by that boundary alone. Writes (v2) are not: they also carry
a per-session secret in the :data:`WRITE_TOKEN_HEADER` header, minted by
``create_app`` at boot and enforced by
:func:`~factory_console.api.write_token.require_write_token`. That is
defence-in-depth *behind* the boundary — it stops another local process or a
drive-by browser request from mutating the project — not a replacement for it.
"""

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Interfaces the console is allowed to bind to. Anything outside this set would move
# the server across the 127.0.0.1 trust boundary, so it is rejected at startup.
LOOPBACK_HOSTS: frozenset[str] = frozenset({"127.0.0.1", "localhost", "::1"})

# The request header carrying the per-session write token. Defined here (beside the
# ``Settings.write_token`` override that pins its value) so the header name has ONE
# home shared by the app factory's stderr announcement, the verifier dependency, and
# the published OpenAPI security scheme.
WRITE_TOKEN_HEADER = "X-Factory-Write-Token"


def require_loopback_host(host: str) -> str:
    """Return ``host`` if it is a loopback address, else raise ``ValueError``.

    The single home of the 127.0.0.1 trust-boundary rule and its message, called by
    both the :class:`Settings` validator and the CLI so the boundary is defined and
    enforced in exactly one place rather than by two copies that can drift.
    """
    if host not in LOOPBACK_HOSTS:
        raise ValueError(f"host must be a loopback address {sorted(LOOPBACK_HOSTS)}, got {host!r}")
    return host


class Settings(BaseSettings):
    """Operational config for the console, loaded from ``FACTORY_CONSOLE_*`` env vars."""

    model_config = SettingsConfigDict(env_prefix="FACTORY_CONSOLE_")

    host: str = "127.0.0.1"
    port: int = 0
    log_level: str = "INFO"

    # An operator/test override for the per-session write token, pinning it to a
    # deterministic value instead of the random one ``create_app`` mints. ``None``
    # (the default, and the normal case) means "generate a fresh token at boot", so
    # the secret is never persisted in config and never survives a restart.
    write_token: str | None = None

    @field_validator("host")
    @classmethod
    def _require_loopback_host(cls, value: str) -> str:
        """Reject any host outside the loopback allow-set (127.0.0.1 trust boundary)."""
        return require_loopback_host(value)
