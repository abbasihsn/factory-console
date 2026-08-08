"""Test-suite-wide fixtures.

Currently one concern: giving every ``TestClient`` a LOOPBACK ``Host`` header.
"""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

# Starlette's TestClient defaults to ``http://testserver``, so every request it
# makes carries ``Host: testserver``. The app now runs TrustedHostMiddleware over
# ``config.LOOPBACK_HOSTS`` (closing the DNS-rebinding hole in the read endpoints'
# loopback-only trust boundary), which correctly refuses that name with a 400 —
# refusing the whole suite along with it.
#
# The Host a TestClient sends is an artifact of the HARNESS, not of the product: no
# real client ever calls this server as ``testserver``, and the ASGI transport it
# uses has no socket to bind. So the default is moved to a loopback name in ONE
# place rather than threaded through ~120 inline ``TestClient(...)`` constructions,
# where it would read as noise and the next new call site would forget it.
#
# A test that deliberately probes host rejection passes ``base_url`` explicitly and
# is unaffected — this only changes the DEFAULT.
_LOOPBACK_BASE_URL = "http://127.0.0.1"


@pytest.fixture(autouse=True, scope="session")
def _testclient_uses_a_loopback_host() -> object:
    """Default every ``TestClient`` to a loopback ``Host`` for the whole session."""
    original_init = TestClient.__init__

    def patched_init(self: TestClient, *args: object, **kwargs: object) -> None:
        kwargs.setdefault("base_url", _LOOPBACK_BASE_URL)
        original_init(self, *args, **kwargs)  # type: ignore[arg-type]

    TestClient.__init__ = patched_init  # type: ignore[method-assign]
    yield
    TestClient.__init__ = original_init  # type: ignore[method-assign]
