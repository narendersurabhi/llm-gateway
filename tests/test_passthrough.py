"""Spec 002 behaviour, written before the implementation.

Every test here is `xfail(strict=True)`: it fails until spec 002 is built, and a test that
starts passing fails the suite, so the marker is removed as each requirement lands (T010).
The tests drive the real HTTP adapter and real outbound adapters against a fake upstream
through the plan's test seam: `create_app(..., gateway_config=..., provider_transport=...)`.
"""

import asyncio
import json
import statistics
import time
from contextlib import contextmanager

import httpx
import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.xfail(strict=True, reason="spec 002: not implemented yet")

KEY = "gw-test-key"
CONFIG = {
    "client_keys": {"orchestrator": KEY},
    "models": {
        "claude-sonnet-5-5": {
            "dialect": "anthropic",
            "capabilities": {"tool_calling": True, "max_input_tokens": 1_000_000,
                             "prompt_caching": "explicit", "thinking": True},
            "placements": [
                {"name": "claude-api-a", "provider": "claude-api", "region": "us",
                 "base_url": "https://a.anthropic.test", "credential_env": "PROVIDER_KEY_A"},
                {"name": "claude-api-b", "provider": "claude-api", "region": "eu",
                 "base_url": "https://b.anthropic.test", "credential_env": "PROVIDER_KEY_B"},
            ],
            "limits": {"requests_per_minute": 1000, "input_tokens_per_minute": 1_000_000,
                       "output_tokens_per_minute": 100_000},
        },
        "qwen-test": {
            "dialect": "openai",
            "capabilities": {"tool_calling": True, "max_input_tokens": 131072,
                             "prompt_caching": "automatic"},
            "placements": [
                {"name": "vllm", "provider": "vllm", "max_in_flight_per_replica": 1,
                 "replicas": ["http://vllm-1.test", "http://vllm-2.test"]},
            ],
        },
    },
}  # fmt: skip
ANTHROPIC_HEADERS = {"x-api-key": KEY, "anthropic-version": "2023-06-01",
                     "content-type": "application/json"}  # fmt: skip
OPENAI_HEADERS = {"authorization": f"Bearer {KEY}", "content-type": "application/json"}
MESSAGE = {"id": "msg_1", "type": "message", "model": "claude-sonnet-5-5", "role": "assistant",
           "content": [{"type": "text", "text": "hi"}], "stop_reason": "end_turn",
           "usage": {"input_tokens": 12, "output_tokens": 3, "cache_read_input_tokens": 900,
                     "cache_creation_input_tokens": 0}}  # fmt: skip
COMPLETION = {"id": "c1", "object": "chat.completion", "model": "qwen-test",
              "choices": [{"index": 0, "message": {"role": "assistant", "content": "hi"}}],
              "usage": {"prompt_tokens": 10, "completion_tokens": 2}}  # fmt: skip


def anthropic_body(**extra) -> bytes:
    # Deliberate key order and spacing: forwarding must not re-serialize.
    body = ('{"model": "claude-sonnet-5-5",  "max_tokens": 64, '
            '"system": [{"type": "text", "text": "S", "cache_control": {"type": "ephemeral"}}], '
            '"tools": [{"name": "b_tool", "input_schema": {"type": "object"}}, '
            '{"name": "a_tool", "input_schema": {"type": "object"}}], '
            '"messages": [{"role": "user", "content": "hello"}]')  # fmt: skip
    for key, value in extra.items():
        body += f', "{key}": {json.dumps(value)}'
    return (body + "}").encode()


class Upstream:
    """Fake provider: records requests, answers with a handler (default: success)."""

    def __init__(self, respond=None):
        self.requests: list[httpx.Request] = []
        self.respond = respond

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.respond:
            return self.respond(request)
        if request.url.path.endswith("/chat/completions"):
            return httpx.Response(200, json=COMPLETION)
        return httpx.Response(200, json=MESSAGE)


@contextmanager
def gateway(monkeypatch, upstream=None, config=CONFIG):
    from llm_gateway.bootstrap import create_app

    monkeypatch.setenv("PROVIDER_KEY_A", "sk-provider-a")
    monkeypatch.setenv("PROVIDER_KEY_B", "sk-provider-b")
    upstream = upstream or Upstream()
    app = create_app(gateway_config=config, provider_transport=httpx.MockTransport(upstream),
                     console_logs=False)  # fmt: skip
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client, upstream


def post_anthropic(client, body=None, **headers):
    return client.post("/anthropic/v1/messages", content=body or anthropic_body(),
                       headers={**ANTHROPIC_HEADERS, **headers})  # fmt: skip


# --- FR-009 / FR-010: pass-through ---------------------------------------------------------


@pytest.mark.spec("FR-009")
def test_anthropic_body_forwarded_byte_for_byte(monkeypatch):
    with gateway(monkeypatch) as (client, upstream):
        resp = post_anthropic(client, **{"anthropic-beta": "some-beta-2026-01-01"})
    assert resp.status_code == 200 and resp.json() == MESSAGE
    assert resp.headers["x-gateway-provider"] == "claude-api"
    [sent] = upstream.requests
    assert sent.content == anthropic_body()
    assert sent.headers["x-api-key"] in {"sk-provider-a", "sk-provider-b"}
    assert sent.headers["anthropic-version"] == "2023-06-01"
    assert sent.headers["anthropic-beta"] == "some-beta-2026-01-01"


@pytest.mark.spec("FR-009")
def test_stream_relayed_in_order(monkeypatch):
    events = b"".join(f'event: e{i}\ndata: {{"i": {i}}}\n\n'.encode() for i in range(5))

    def stream(_):
        return httpx.Response(200, content=events, headers={"content-type": "text/event-stream"})

    with (
        gateway(monkeypatch, Upstream(stream)) as (client, _),
        client.stream(
            "POST", "/anthropic/v1/messages", headers=ANTHROPIC_HEADERS,
            content=anthropic_body(stream=True),
        ) as resp,
    ):  # fmt: skip
        body = b"".join(resp.iter_bytes())
    assert resp.headers["content-type"].startswith("text/event-stream")
    assert body == events


@pytest.mark.spec("FR-010")
def test_openai_compatible_forwarded_unchanged(monkeypatch):
    body = b'{"model": "qwen-test", "messages": [{"role": "user", "content": "hi"}], "tools": []}'
    with gateway(monkeypatch) as (client, upstream):
        resp = client.post("/openai/v1/chat/completions", content=body, headers=OPENAI_HEADERS)
    assert resp.status_code == 200 and resp.json() == COMPLETION
    [sent] = upstream.requests
    assert sent.content == body
    assert sent.url.host in {"vllm-1.test", "vllm-2.test"}
    assert "authorization" not in sent.headers or KEY not in sent.headers["authorization"]


# --- FR-011: discovery and dialect checks ---------------------------------------------------


@pytest.mark.spec("FR-011")
def test_models_listed_and_checked(monkeypatch):
    with gateway(monkeypatch) as (client, upstream):
        listed = client.get("/v1/models", headers={"x-api-key": KEY}).json()
        unknown = post_anthropic(client, anthropic_body().replace(b"claude-sonnet-5-5", b"nope"))
        wrong_dialect = {
            "model": "claude-sonnet-5-5",
            "messages": [{"role": "user", "content": "x"}],
        }
        wrong = client.post(
            "/openai/v1/chat/completions", headers=OPENAI_HEADERS, json=wrong_dialect
        )
    assert {m["id"]: m["dialect"] for m in listed["data"]} == {
        "claude-sonnet-5-5": "anthropic", "qwen-test": "openai"}  # fmt: skip
    assert unknown.status_code == 404 and unknown.json()["type"] == "error"
    assert wrong.status_code == 400 and "error" in wrong.json()
    assert upstream.requests == []


# --- FR-012: placement ---------------------------------------------------------------------


@pytest.mark.spec("FR-012")
def test_thread_affinity_and_constraint(monkeypatch):
    with gateway(monkeypatch) as (client, upstream):
        for _ in range(10):
            post_anthropic(client, **{"x-agent-thread": "thread-1"})
        hosts_one = {r.url.host for r in upstream.requests}
        upstream.requests.clear()
        for i in range(40):
            post_anthropic(client, **{"x-agent-thread": f"t{i}"})
        hosts_many = {r.url.host for r in upstream.requests}
        upstream.requests.clear()
        post_anthropic(client, **{"x-gateway-placement": "eu"})
        eu_host = upstream.requests[-1].url.host
    assert len(hosts_one) == 1
    assert hosts_many == {"a.anthropic.test", "b.anthropic.test"}
    assert eu_host == "b.anthropic.test"


@pytest.mark.spec("FR-012")
def test_failover_keeps_the_model(monkeypatch):
    def flaky(request):
        if request.url.host == "a.anthropic.test":
            error = {"type": "overloaded_error", "message": "busy"}
            return httpx.Response(529, json={"type": "error", "error": error})
        return httpx.Response(200, json=MESSAGE)

    with gateway(monkeypatch, Upstream(flaky)) as (client, upstream):
        resps = [post_anthropic(client, **{"x-agent-thread": f"t{i}"}) for i in range(10)]
    assert all(r.status_code == 200 for r in resps)
    assert all(json.loads(r.content)["model"] == "claude-sonnet-5-5" for r in upstream.requests)


# --- FR-013 / FR-014: capacity --------------------------------------------------------------


@pytest.mark.spec("FR-013")
def test_quota_exhaustion_is_429_with_retry_after(monkeypatch):
    config = json.loads(json.dumps(CONFIG))
    config["models"]["claude-sonnet-5-5"]["limits"]["requests_per_minute"] = 2
    with gateway(monkeypatch, config=config) as (client, _):
        statuses = [post_anthropic(client).status_code for _ in range(4)]
        limited = post_anthropic(client)
    assert statuses[:2] == [200, 200] and 429 in statuses[2:]
    assert int(limited.headers["retry-after"]) >= 1
    assert limited.json()["error"]["type"] == "rate_limit_error"


@pytest.mark.spec("FR-013")
def test_cache_reads_do_not_consume_input_quota(monkeypatch):
    config = json.loads(json.dumps(CONFIG))
    config["models"]["claude-sonnet-5-5"]["limits"]["input_tokens_per_minute"] = 100
    with gateway(monkeypatch, config=config) as (client, _):
        # MESSAGE.usage: 12 uncached + 900 cache-read tokens per call.
        statuses = [post_anthropic(client).status_code for _ in range(5)]
    assert statuses == [200] * 5


@pytest.mark.spec("FR-014")
def test_self_hosted_concurrency_limit(monkeypatch):
    async def slow(request):
        await asyncio.sleep(0.5)
        return httpx.Response(200, json=COMPLETION)

    from llm_gateway.bootstrap import create_app

    app = create_app(gateway_config=CONFIG, provider_transport=httpx.MockTransport(slow),
                     console_logs=False)  # fmt: skip
    body = {"model": "qwen-test", "messages": [{"role": "user", "content": "hi"}]}

    async def burst():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://gw") as c:
            calls = [
                c.post("/openai/v1/chat/completions", json=body, headers=OPENAI_HEADERS)
                for _ in range(3)
            ]
            return await asyncio.gather(*calls)

    statuses = sorted(r.status_code for r in asyncio.run(burst()))
    assert statuses == [200, 200, 429]  # two replicas, one in flight each


# --- FR-015 / FR-016: auth and deadlines ----------------------------------------------------


@pytest.mark.spec("FR-015")
def test_missing_or_unknown_key_is_rejected(monkeypatch):
    with gateway(monkeypatch) as (client, upstream):
        missing = client.post("/anthropic/v1/messages", content=anthropic_body(),
                              headers={"anthropic-version": "2023-06-01"})  # fmt: skip
        wrong = post_anthropic(client, **{"x-api-key": "nope"})
    for resp in (missing, wrong):
        assert resp.status_code == 401 and resp.json()["error"]["type"] == "authentication_error"
    assert upstream.requests == []


@pytest.mark.spec("FR-016")
def test_past_deadline_is_504_without_upstream_call(monkeypatch):
    with gateway(monkeypatch) as (client, upstream):
        resp = post_anthropic(client, **{"x-agent-deadline": "2000-01-01T00:00:00Z"})
    assert resp.status_code == 504
    assert upstream.requests == []


# --- FR-017 / NFR-005 / NFR-006: telemetry and overhead -------------------------------------


@pytest.mark.spec("FR-017")
def test_span_and_token_metrics_include_cache_types(monkeypatch, spans, collect_metrics):
    with gateway(monkeypatch) as (client, _):
        collect_metrics()
        post_anthropic(client, **{"x-agent-route": "interactive"})
        metrics = collect_metrics()
    [chat] = [s for s in spans.get_finished_spans() if s.name == "chat claude-sonnet-5-5"]
    assert chat.attributes["gen_ai.provider.name"] == "anthropic"
    assert chat.attributes["gen_ai.usage.input_tokens"] == 12
    types = {p.attributes["gen_ai.token.type"]
             for p in metrics["gen_ai.client.token.usage"].data.data_points}  # fmt: skip
    assert types == {"input", "output", "cache_read", "cache_creation"}


@pytest.mark.spec("NFR-005")
def test_no_content_or_keys_in_telemetry(monkeypatch, spans, logs):
    secret = "zebra-sapphire-7731"
    body = anthropic_body().replace(b"hello", secret.encode())
    with gateway(monkeypatch) as (client, _):
        post_anthropic(client, body)
    dumped = json.dumps([dict(s.attributes or {}) for s in spans.get_finished_spans()]
                        + [str(r.log_record.body) for r in logs.get_finished_logs()]
                        + [dict(r.log_record.attributes or {})
                           for r in logs.get_finished_logs()], default=str)  # fmt: skip
    for needle in (secret, KEY, "sk-provider-a", "sk-provider-b"):
        assert needle not in dumped


@pytest.mark.spec("NFR-006")
def test_overhead_is_small(monkeypatch):
    with gateway(monkeypatch) as (client, _):
        for _ in range(20):
            post_anthropic(client)  # warm up
        timings = []
        for _ in range(200):
            start = time.perf_counter()
            post_anthropic(client)
            timings.append(time.perf_counter() - start)
    p99 = statistics.quantiles(timings, n=100)[98]
    assert p99 < 0.010, f"p99 {p99 * 1000:.1f} ms"
