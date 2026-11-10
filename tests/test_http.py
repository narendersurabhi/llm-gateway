"""Health, error envelope and contract conformance of the HTTP adapter."""

import re
from pathlib import Path

import pytest
import yaml
from conftest import FakeClock
from fastapi import Body
from jsonschema import Draft202012Validator

from llm_gateway import __version__
from llm_gateway.application.health import CheckHealth
from llm_gateway.domain import errors

ROOT = Path(__file__).resolve().parent.parent
CONTRACT = yaml.safe_load((ROOT / "contracts" / "openapi.yaml").read_text())


def validator(schema_name: str) -> Draft202012Validator:
    return Draft202012Validator(
        {"$ref": f"#/components/schemas/{schema_name}", "components": CONTRACT["components"]}
    )


# --- FR-001: health -------------------------------------------------------------------------


@pytest.mark.spec("FR-001")
def test_check_health_use_case_with_fake_clock():
    clock = FakeClock(10.0)
    check = CheckHealth(clock, "9.9.9")
    clock.now = 12.25
    health = check()
    assert (health.status, health.version, health.uptime_s) == ("ok", "9.9.9", 2.25)


@pytest.mark.spec("FR-001", "FR-006")
def test_healthz(make_client):
    clock = FakeClock(100.0)
    with make_client(clock=clock) as (client, _):
        clock.now = 112.5
        resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "version": __version__, "uptime_s": 12.5}
    validator("Health").validate(resp.json())


# --- FR-002: error envelope -----------------------------------------------------------------

CASES = [
    (errors.InvalidRequest("bad thing"), 400),
    (errors.Unauthenticated(), 401),
    (errors.Forbidden(), 403),
    (errors.NotFound(), 404),
    (errors.Conflict(), 409),
    (errors.RateLimited(retry_after_s=7), 429),
    (errors.DomainError(), 500),
    (errors.UpstreamError(), 502),
    (errors.Unavailable(retry_after_s=2.4), 503),
    (errors.DeadlineExceeded(), 504),
]


@pytest.mark.spec("FR-002", "FR-006")
@pytest.mark.parametrize(("error", "status"), CASES, ids=[type(e).__name__ for e, _ in CASES])
def test_domain_errors_map_to_envelope(make_client, error, status):
    with make_client() as (client, app):

        def boom():
            raise error

        app.add_api_route("/boom", boom)
        resp = client.get("/boom")
    assert resp.status_code == status
    body = resp.json()
    validator("Error").validate(body)
    assert body["error"] == {"type": error.type, "message": error.public_message}
    assert re.fullmatch(r"[0-9a-f]{32}", body["trace_id"])
    if error.retry_after_s:
        assert resp.headers["retry-after"] == str(max(1, round(error.retry_after_s)))
    if status == 401:
        assert resp.headers["www-authenticate"] == "Bearer"


@pytest.mark.spec("FR-002")
def test_validation_errors_name_fields_without_echoing_values(make_client):
    with make_client() as (client, app):

        def create(name: str = Body(..., embed=True, max_length=3)):
            return {"name": name}

        app.add_api_route("/things", create, methods=["POST"])
        resp = client.post("/things", json={"name": "secret-value-xyz"})
    assert resp.status_code == 422
    body = resp.json()
    validator("Error").validate(body)
    assert body["error"]["type"] == "invalid_request"
    assert "body.name" in body["error"]["message"]
    assert "secret-value-xyz" not in resp.text


@pytest.mark.spec("FR-002")
def test_unknown_route_and_method(make_client):
    with make_client() as (client, _):
        missing = client.get("/nope")
        wrong_method = client.delete("/healthz")
    assert missing.status_code == 404 and missing.json()["error"]["type"] == "not_found"
    assert wrong_method.status_code == 405
    assert wrong_method.json()["error"]["type"] == "invalid_request"


@pytest.mark.spec("FR-002")
def test_unhandled_exception_is_500_without_details(make_client):
    with make_client() as (client, app):

        def crash():
            raise RuntimeError("database password is hunter2")

        app.add_api_route("/crash", crash)
        resp = client.get("/crash")
    assert resp.status_code == 500
    validator("Error").validate(resp.json())
    assert resp.json()["error"] == {"type": "internal", "message": "internal error"}
    assert "hunter2" not in resp.text
