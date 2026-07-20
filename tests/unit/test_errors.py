"""Unit tests for :mod:`factory_console.errors`.

Pin the REST v1 error-envelope contract: ``details`` is present iff it is not
``None`` (a falsy-but-not-None value is still emitted), and ``code``/``message``
pass through unchanged.
"""

from factory_console.errors import FactoryConsoleError, to_error_response


def test_details_omitted_when_none() -> None:
    exc = FactoryConsoleError(code="not_found", message="missing", status=404)
    response = to_error_response(exc)
    assert response == {"error": {"code": "not_found", "message": "missing"}}
    assert "details" not in response["error"]


def test_details_included_when_set() -> None:
    exc = FactoryConsoleError(
        code="invalid", message="bad field", status=400, details={"field": "host"}
    )
    response = to_error_response(exc)
    assert response == {
        "error": {"code": "invalid", "message": "bad field", "details": {"field": "host"}}
    }


def test_falsy_non_none_details_are_included() -> None:
    exc = FactoryConsoleError(code="empty", message="no context", status=400, details=[])
    response = to_error_response(exc)
    assert response["error"]["details"] == []
    assert "details" in response["error"]


def test_code_and_message_pass_through() -> None:
    exc = FactoryConsoleError(code="conflict", message="already exists", status=409)
    response = to_error_response(exc)
    assert response["error"]["code"] == "conflict"
    assert response["error"]["message"] == "already exists"
