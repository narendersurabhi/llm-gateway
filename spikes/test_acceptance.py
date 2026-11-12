"""Gateway framework spike: spec 002's must-haves as black-box checks against a running gateway.

Not part of the default suite (marked `live`). Run with run_spike.sh, which starts the
WireMock upstreams and one gateway, then:

    SPIKE_GATEWAY=litellm SPIKE_BASE_URL=http://localhost:4000 ... uv run pytest spikes -m live

Upstreams (WireMock): anthropic-a :9101, anthropic-b :9102, vllm-1 :9103, vllm-2 :9104.
Each check is independent, so the report shows exactly which requirements a framework meets.
"""

import json
import os
import statistics
import time
import uuid
from pathlib import Path

import httpx
import pytest

pytestmark = pytest.mark.live

GATEWAY = os.environ.get("SPIKE_GATEWAY", "unknown")
BASE = os.environ.get("SPIKE_BASE_URL", "http://localhost:4000")
ANTHROPIC_PATH = os.environ.get("SPIKE_ANTHROPIC_PATH", "/v1/messages")
OPENAI_PATH = os.environ.get("SPIKE_OPENAI_PATH", "/v1/chat/completions")
CLIENT_KEY = os.environ.get("SPIKE_CLIENT_KEY", "sk-spike-client-key")
# The conversation header each gateway keys affinity on (ours is x-agent-thread).
THREAD = os.environ.get("SPIKE_THREAD_HEADER", "x-agent-thread")
PROVIDER_KEYS = {9101: "sk-provider-a", 9102: "sk-provider-b"}
ANTHROPIC = (9101, 9102)
VLLM = (9103, 9104)
EXPECTED = json.loads((Path(__file__).parent / "expected.json").read_text())
HEADERS = {"x-api-key": CLIENT_KEY, "anthropic-version": "2023-06-01",
           "content-type": "application/json"}  # fmt: skip


def body(model: str = "claude-sonnet-5-5", text: str = "hello", stream: bool = False) -> bytes:
    """Unusual spacing and key order, a 1h cache TTL, and tools in non-alphabetical order:
    anything a gateway re-serializes or normalizes shows up as a difference."""
    raw = (
        '{"model": "MODEL",  "max_tokens": 64, '
        '"system": [{"type": "text", "text": "You are terse.", '
        '"cache_control": {"type": "ephemeral", "ttl": "1h"}}], '
        '"tools": [{"name": "zeta_tool", "description": "z", '
        '"input_schema": {"type": "object", "properties": {"b": {"type": "string"}, '
        '"a": {"type": "string"}}}}, {"name": "alpha_tool", "description": "a", '
        '"input_schema": {"type": "object"}, "cache_control": {"type": "ephemeral"}}], '
        '"messages": [{"role": "user", "content": [{"type": "text", "text": "TEXT", '
        '"cache_control": {"type": "ephemeral"}}]}]STREAM}'
    )
    raw = raw.replace("MODEL", model).replace("TEXT", text)
    return raw.replace("STREAM", ', "stream": true' if stream else "").encode()


def journal(port: int) -> list[dict]:
    reqs = httpx.get(f"http://localhost:{port}/__admin/requests", timeout=10).json()["requests"]
    return [r["request"] for r in reqs]


def reset(*ports: int) -> None:
    for port in ports:
        httpx.delete(f"http://localhost:{port}/__admin/requests", timeout=10)


def received(ports) -> list[tuple[int, dict]]:
    return [(port, r) for port in ports for r in journal(port)]


def post(path=ANTHROPIC_PATH, content=None, headers=None, **kw) -> httpx.Response:
    merged = {**HEADERS, **(headers or {})}
    return httpx.post(BASE + path, content=content or body(), headers=merged, timeout=30, **kw)


@pytest.fixture(autouse=True)
def _clean():
    reset(*ANTHROPIC, *VLLM)
    yield


# --- S1–S3: pass-through fidelity ---------------------------------------------------------


def test_s1_body_forwarded_byte_for_byte():
    sent = body()
    assert post(content=sent).status_code == 200
    [(_, req)] = received(ANTHROPIC)
    assert req["body"].encode() == sent, "body re-serialized or modified"


def test_s1b_body_semantically_unchanged():
    """Weaker than S1: key order and whitespace may differ and an explicit `"stream": false`
    may be added (it is not part of the cached prompt), but every other field, including the
    1h cache TTL, every cache_control marker and the tool order, survives."""
    sent = body()
    assert post(content=sent).status_code == 200
    [(_, req)] = received(ANTHROPIC)
    got = json.loads(req["body"])
    if got.get("stream") is False:
        del got["stream"]
    assert got == json.loads(sent)


def test_s2_response_and_usage_unchanged():
    resp = post()
    assert resp.status_code == 200
    assert resp.json() == EXPECTED["message"]


def test_s3_stream_relayed_unchanged():
    with httpx.stream("POST", BASE + ANTHROPIC_PATH, content=body(stream=True),
                      headers=HEADERS, timeout=30) as resp:  # fmt: skip
        data = b"".join(resp.iter_bytes()).decode()
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    assert data == EXPECTED["events"]


# --- S4–S6: credentials and dialects --------------------------------------------------------


def test_s4_provider_key_injected_and_client_key_not_forwarded():
    assert post().status_code == 200
    [(port, req)] = received(ANTHROPIC)
    headers = {k.lower(): v for k, v in req["headers"].items()}
    assert headers.get("x-api-key") == PROVIDER_KEYS[port]
    assert CLIENT_KEY not in json.dumps(headers)


def test_s5_client_key_required():
    keyless = {k: v for k, v in HEADERS.items() if k != "x-api-key"}
    resp = httpx.post(BASE + ANTHROPIC_PATH, content=body(), headers=keyless, timeout=30)
    assert resp.status_code in (401, 403), resp.status_code
    assert received(ANTHROPIC) == []


def test_s6_openai_compatible_to_self_hosted():
    sent = b'{"model": "qwen-test", "messages": [{"role": "user", "content": "hi"}], "tools": []}'
    resp = post(OPENAI_PATH, content=sent,
                headers={"authorization": f"Bearer {CLIENT_KEY}"})  # fmt: skip
    assert resp.status_code == 200, resp.text[:300]
    assert resp.json()["choices"][0]["message"]["content"] == "hi"
    [(_, req)] = received(VLLM)
    got = json.loads(req["body"])
    if got.get("stream") is False:
        del got["stream"]
    assert got == json.loads(sent)


# --- S7–S8: placement ---------------------------------------------------------------------


def test_s7_thread_affinity():
    thread = f"thread-{uuid.uuid4().hex[:8]}"
    for _ in range(10):
        assert post(headers={THREAD: thread}).status_code == 200
    ports = {port for port, _ in received(ANTHROPIC)}
    assert len(ports) == 1, f"one thread was spread over {sorted(ports)}"
    reset(*ANTHROPIC)
    for i in range(20):
        post(headers={THREAD: f"t-{i}-{uuid.uuid4().hex[:6]}"})
    assert {port for port, _ in received(ANTHROPIC)} == set(ANTHROPIC), "no spread across threads"


def test_s8_failover_keeps_the_model():
    stub = {"priority": 0, "request": {"method": "POST", "urlPath": "/v1/messages"},
            "response": {"status": 529, "jsonBody": {"type": "error", "error": {
                "type": "overloaded_error", "message": "busy"}}}}  # fmt: skip
    created = httpx.post("http://localhost:9101/__admin/mappings", json=stub, timeout=10).json()
    try:
        statuses = [post(headers={THREAD: f"f{i}"}).status_code for i in range(8)]
    finally:
        httpx.delete(f"http://localhost:9101/__admin/mappings/{created['id']}", timeout=10)
    assert statuses == [200] * 8, statuses
    forwarded = [json.loads(r["body"])["model"] for _, r in received(ANTHROPIC)]
    assert set(forwarded) == {"claude-sonnet-5-5"}
    assert any(port == 9102 for port, _ in received(ANTHROPIC))


# --- S9: cache-aware token limits ----------------------------------------------------------


def test_s9_cache_reads_do_not_count_but_uncached_input_does():
    """Model `claude-limited` (anthropic-a only) is configured with ~2000 input tokens/min.
    Five cached calls total 4560 input tokens but only 60 uncached: all must pass. Then two
    calls of 5000 uncached tokens: the second must be refused."""
    cached = [post(content=body(model="claude-limited")).status_code for _ in range(5)]
    big1 = post(content=body(model="claude-limited", text="BIG-UNCACHED")).status_code
    big2 = post(content=body(model="claude-limited", text="BIG-UNCACHED"))
    assert cached == [200] * 5, f"cached calls throttled: {cached}"
    assert big1 == 200
    assert big2.status_code == 429, f"uncached overrun not limited: {big2.status_code}"
    assert "retry-after" in {k.lower() for k in big2.headers}


# --- S10–S11: telemetry and overhead ----------------------------------------------------------


def test_s10_no_prompt_content_in_traces():
    marker = f"zebra-sapphire-{uuid.uuid4().hex[:8]}"
    assert post(content=body(text=marker)).status_code == 200
    service = os.environ.get("SPIKE_OTEL_SERVICE", GATEWAY)
    deadline = time.time() + 40
    traces = []
    while time.time() < deadline and not traces:
        time.sleep(4)
        search = httpx.get("http://localhost:3000/api/datasources/proxy/uid/tempo/api/search",
                           params={"tags": f"service.name={service}", "limit": 50,
                                   "start": int(time.time()) - 600, "end": int(time.time())},
                           timeout=10).json()  # fmt: skip
        traces = search.get("traces", [])
    assert traces, f"no traces from service.name={service} (cannot verify)"
    dumps = [httpx.get(f"http://localhost:3000/api/datasources/proxy/uid/tempo/api/traces/"
                       f"{t['traceID']}", timeout=10).text for t in traces]  # fmt: skip
    assert not any(marker in d for d in dumps), "prompt content found in exported spans"


def test_s11_overhead():
    def timed(url, headers):
        samples = []
        for _ in range(150):
            start = time.perf_counter()
            r = httpx.post(url, content=body(), headers=headers, timeout=30)
            samples.append(time.perf_counter() - start)
            assert r.status_code == 200
        return statistics.quantiles(samples[20:], n=100)

    direct = timed("http://localhost:9101/v1/messages", HEADERS)
    via = timed(BASE + ANTHROPIC_PATH, HEADERS)
    added_p50 = (via[49] - direct[49]) * 1000
    added_p99 = (via[98] - direct[98]) * 1000
    print(f"\n{GATEWAY}: added latency p50 {added_p50:.1f} ms, p99 {added_p99:.1f} ms")
    assert added_p99 < 10, f"p99 overhead {added_p99:.1f} ms"
