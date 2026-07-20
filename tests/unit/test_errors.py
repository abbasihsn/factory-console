"""Tests for the REST v1 error envelope produced by ``to_error_response``."""

from factory_console.errors import FactoryConsoleError, to_error_response


def test_error_response_omits_details_when_none() -> None:
    """With no details, the envelope carries only ``code`` and ``message``."""
    exc = FactoryConsoleError(code="not_found", message="missing", status=404)
    body = to_error_response(exc)
    assert body == {"error": {"code": "not_found", "message": "missing"}}
    assert "details" not in body["error"]


def test_error_response_includes_details_when_set() -> None:
    """When details are provided, they appear verbatim in the envelope."""
    details = {"field": "host"}
    exc = FactoryConsoleError(
        code="invalid", message="bad host", status=400, details=details
    )
    body = to_error_response(exc)
    assert body == {
        "error": {"code": "invalid", "message": "bad host", "details": details}
    }


def test_error_fields_pass_through() -> None:
    """Constructor arguments are stored on the exception unchanged."""
    exc = FactoryConsoleError(code="conflict", message="dupe", status=409)
    assert exc.code == "conflict"
    assert exc.message == "dupe"
    assert exc.status == 409
    assert exc.details is None
