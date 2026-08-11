from fastapi import Request
from fastapi.testclient import TestClient

import app as app_module


def test_session_cookie_is_set_and_readable(monkeypatch):
    """Smoke test for the middleware itself: a route that writes to
    request.session should produce a session cookie the same client can read
    back on a later request."""

    @app_module.app.get("/__test_session_probe__")
    def _probe(request: Request):
        from starlette.requests import Request as StarletteRequest

        assert isinstance(request, StarletteRequest)
        request.session["probe"] = "hello"
        return {"ok": True}

    @app_module.app.get("/__test_session_read__")
    def _read(request: Request):
        return {"probe": request.session.get("probe")}

    client = TestClient(app_module.app)
    client.get("/__test_session_probe__")
    response = client.get("/__test_session_read__")
    assert response.json() == {"probe": "hello"}
