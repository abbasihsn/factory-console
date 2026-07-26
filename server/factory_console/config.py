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

``FACTORY_CONSOLE_WRITE_TOKEN`` optionally pins that secret; boot paths read it
through :func:`read_write_token`, which reads the token WITHOUT re-validating the
other fields, so an unrelated host/port/log-level in the environment cannot fail a
boot that never consulted it.
"""

from pydantic import ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Interfaces the console is allowed to bind to. Anything outside this set would move
# the server across the 127.0.0.1 trust boundary, so it is rejected at startup.
LOOPBACK_HOSTS: frozenset[str] = frozenset({"127.0.0.1", "localhost", "::1"})

# The request header carrying the per-session write token. Defined here (beside the
# ``Settings.write_token`` override that pins its value) so the header name has ONE
# home shared by the app factory's stderr announcement, the verifier dependency, and
# the published OpenAPI security scheme.
WRITE_TOKEN_HEADER = "X-Factory-Write-Token"

# Shortest write-token pin an operator may set. ``create_app`` mints 32 random bytes;
# a pin is hand-managed, so this is the floor that keeps it an actual secret rather
# than something brute-forceable against a write route that has no rate limiting.
MIN_WRITE_TOKEN_CHARS = 16

# The one wording for a rejected pin, shared by the validator and :func:`read_write_token`
# so the operator sees the same sentence whichever path read the value.
_WRITE_TOKEN_PIN_ERROR = (
    f"FACTORY_CONSOLE_WRITE_TOKEN must be at least {MIN_WRITE_TOKEN_CHARS} characters, "
    "or unset to mint a fresh token at every boot"
)


def require_loopback_host(host: str) -> str:
    """Return ``host`` if it is a loopback address, else raise ``ValueError``.

    The single home of the 127.0.0.1 trust-boundary rule and its message, called by
    both the :class:`Settings` validator and the CLI so the boundary is defined and
    enforced in exactly one place rather than by two copies that can drift.
    """
    if host not in LOOPBACK_HOSTS:
        raise ValueError(f"host must be a loopback address {sorted(LOOPBACK_HOSTS)}, got {host!r}")
    return host


def require_write_token_pin(value: str | None) -> str | None:
    """Return the write-token pin ``value``, stripped, or ``None`` to mint one at boot.

    The single home of the pin rule, in the same spirit as
    :func:`require_loopback_host`. ``None`` — the variable is unset, and the normal
    case — means "generate a fresh token per boot". But a variable that IS set must
    carry a real secret, so a blank or too-short pin raises rather than falling back
    to a generated token: that fallback would silently discard the operator's pin and
    leave every write answering the same opaque 401 with nothing to diagnose it by.
    """
    if value is None:
        return None
    token = value.strip()
    if len(token) < MIN_WRITE_TOKEN_CHARS:
        raise ValueError(_WRITE_TOKEN_PIN_ERROR)
    return token


class WriteTokenSettings(BaseSettings):
    """Just the write-token pin, readable without touching the rest of the config.

    Split out of :class:`Settings` so a caller that needs only the token does not
    re-validate ``host``/``port``/``log_level`` as a side effect — see
    :func:`read_write_token`, which is why this exists. :class:`Settings` inherits it,
    so the field and its validator are still declared exactly once.
    """

    model_config = SettingsConfigDict(env_prefix="FACTORY_CONSOLE_")

    # An operator/test override for the per-session write token, pinning it to a
    # deterministic value instead of the random one ``create_app`` mints. ``None``
    # (the default, and the normal case) means "generate a fresh token at boot", so
    # the secret is never persisted in config and never survives a restart.
    write_token: str | None = None

    @field_validator("write_token")
    @classmethod
    def _require_write_token_pin(cls, value: str | None) -> str | None:
        """Reject a blank or too-short pin instead of silently generating a token."""
        return require_write_token_pin(value)


class Settings(WriteTokenSettings):
    """Operational config for the console, loaded from ``FACTORY_CONSOLE_*`` env vars."""

    host: str = "127.0.0.1"
    port: int = 0
    log_level: str = "INFO"

    @field_validator("host")
    @classmethod
    def _require_loopback_host(cls, value: str) -> str:
        """Reject any host outside the loopback allow-set (127.0.0.1 trust boundary)."""
        return require_loopback_host(value)


def read_write_token() -> str | None:
    """Return the validated ``FACTORY_CONSOLE_WRITE_TOKEN`` pin, or ``None`` to mint one.

    The one way a boot path should read the token. It deliberately does NOT build a
    full :class:`Settings`: that validates every ``FACTORY_CONSOLE_*`` field, so an
    unrelated non-loopback ``FACTORY_CONSOLE_HOST`` in the environment would abort a
    boot whose host the caller had already settled — the CLI's ``--host`` beats the
    env var, and ``create_dev_app`` never chooses a host at all (``scripts/dev.sh``
    passes it straight to uvicorn).

    Raises:
        ValueError: The pin is set but blank or shorter than
            :data:`MIN_WRITE_TOKEN_CHARS`. Raised as a plain ``ValueError`` (not
            pydantic's ``ValidationError``) so the CLI reports it through the same
            exit-2 path as a bad host or log level.
    """
    try:
        return WriteTokenSettings().write_token
    except ValidationError as exc:
        raise ValueError(_WRITE_TOKEN_PIN_ERROR) from exc
