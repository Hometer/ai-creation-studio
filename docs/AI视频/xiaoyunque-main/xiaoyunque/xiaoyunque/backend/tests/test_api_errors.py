import logging

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import BaseModel

from api.errors import register_error_handlers


def make_client():
    app = FastAPI()
    register_error_handlers(app)

    class Input(BaseModel):
        count: int

    @app.post("/validate")
    def validate(body: Input):
        return body

    @app.get("/http-error")
    def http_error():
        raise HTTPException(400, "private-test-input")

    @app.get("/unexpected-error")
    def unexpected_error():
        raise RuntimeError("private-test-input")

    return TestClient(app, raise_server_exceptions=False)


def test_validation_response_does_not_echo_input():
    response = make_client().post("/validate", json={"count": "private-test-input"})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert response.json()["detail"] == response.json()["error"]["message"]
    assert "private-test-input" not in response.text


def test_http_error_is_compatible_and_safe():
    response = make_client().get("/http-error")
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "BAD_REQUEST"
    assert "private-test-input" not in response.text


def test_unexpected_exception_is_safe_in_response_and_log(caplog):
    with caplog.at_level(logging.ERROR):
        response = make_client().get("/unexpected-error")
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "INTERNAL_ERROR"
    assert "private-test-input" not in response.text + caplog.text
    assert "RuntimeError" in caplog.text


def test_missing_route_uses_same_contract():
    response = make_client().get("/missing")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"
