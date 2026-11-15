"""Acceptance suite for the gateway on Envoy AI Gateway (spec 002).

Black-box checks against a running gateway with WireMock upstreams. Marked `live`: they need
the kind environment from `deploy/kind/acceptance.sh`, which installs the pinned gateway,
port-forwards the services below and runs this file. Promoted from the framework spike
(branch `chore/gateway-framework-spike`), where the same checks compared LiteLLM and Envoy.

Ports: gateway :1975, WireMock anthropic-a :9101, anthropic-b :9102, vllm-1 :9103,
vllm-2 :9104, Grafana :3000.
"""

import json
import os
import re
import subprocess
import time
import uuid
from pathlib import Path

import httpx
import pytest

pytestmark = pytest.mark.live

GATEWAY = os.environ.get("GATEWAY_NAME", "envoy-ai-gateway")
BASE = os.environ.get("GATEWAY_BASE_URL", "http://localhost:1975")
ANTHROPIC_PATH = os.environ.get("GATEWAY_ANTHROPIC_PATH", "/anthropic/v1/messages")
OPENAI_PATH = os.environ.get("GATEWAY_OPENAI_PATH", "/v1/chat/completions")
CLIENT_KEY = os.environ.get("GATEWAY_CLIENT_KEY", "sk-spike-client-key")
# The conversation header each gateway keys affinity on (ours is x-agent-thread).
THREAD = os.environ.get("GATEWAY_THREAD_HEADER", "x-agent-thread")
PROVIDER_KEYS = {9101: "sk-provider-a", 9102: "sk-provider-b"}
ANTHROPIC = (9101, 9102)
VLLM = (9103, 9104)
EXPECTED = json.loads((Path(__file__).parent / "fixtures" / "expected.json").read_text())
# S12/S13: where per-request usage records live (Envoy access logs, "envoy-k8s"),
# the admin key to read them, and a client key whose budget is ~1 request.
USAGE_SOURCE = os.environ.get("GATEWAY_USAGE_SOURCE", "envoy-k8s")
ADMIN_KEY = os.environ.get("GATEWAY_ADMIN_KEY", "")
BUDGET_KEY = os.environ.get("GATEWAY_BUDGET_KEY", "sk-spike-budget-key")
CLIENT_NAME = os.environ.get("GATEWAY_CLIENT_NAME", "orchestrator")
NAMESPACE = os.environ.get("GATEWAY_NAMESPACE", "spike")
IN_CLUSTER_URL = os.environ.get("GATEWAY_IN_CLUSTER_URL", "")  # set by acceptance.sh
FORTIO_IMAGE = "fortio/fortio:1.76.0"
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


@pytest.mark.spec("FR-009")
def test_s1_body_forwarded_byte_for_byte():
    sent = body()
    assert post(content=sent).status_code == 200
    [(_, req)] = received(ANTHROPIC)
    assert req["body"].encode() == sent, "body re-serialized or modified"


@pytest.mark.spec("FR-009")
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


@pytest.mark.spec("FR-009")
def test_s2_response_and_usage_unchanged():
    resp = post()
    assert resp.status_code == 200
    assert resp.json() == EXPECTED["message"]


@pytest.mark.spec("FR-009")
def test_s3_stream_relayed_unchanged():
    with httpx.stream("POST", BASE + ANTHROPIC_PATH, content=body(stream=True),
                      headers=HEADERS, timeout=30) as resp:  # fmt: skip
        data = b"".join(resp.iter_bytes()).decode()
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    assert data == EXPECTED["events"]


# --- S4–S6: credentials and dialects --------------------------------------------------------


@pytest.mark.spec("FR-009", "FR-015")
def test_s4_provider_key_injected_and_client_key_not_forwarded():
    assert post().status_code == 200
    [(port, req)] = received(ANTHROPIC)
    headers = {k.lower(): v for k, v in req["headers"].items()}
    assert headers.get("x-api-key") == PROVIDER_KEYS[port]
    assert CLIENT_KEY not in json.dumps(headers)


@pytest.mark.spec("FR-015")
def test_s5_client_key_required():
    keyless = {k: v for k, v in HEADERS.items() if k != "x-api-key"}
    resp = httpx.post(BASE + ANTHROPIC_PATH, content=body(), headers=keyless, timeout=30)
    assert resp.status_code in (401, 403), resp.status_code
    assert received(ANTHROPIC) == []


@pytest.mark.spec("FR-010")
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


@pytest.mark.spec("FR-012")
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


@pytest.mark.spec("FR-012")
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


@pytest.mark.spec("FR-013")
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


@pytest.mark.spec("FR-017", "NFR-005")
def test_s10_no_prompt_content_in_traces():
    marker = f"zebra-sapphire-{uuid.uuid4().hex[:8]}"
    assert post(content=body(text=marker)).status_code == 200
    service = os.environ.get("GATEWAY_OTEL_SERVICE", "envoy-k8s-spike")
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


@pytest.mark.spec("NFR-006")
def test_s11_overhead_in_cluster():
    """Port-forwarding adds jitter, so the load tester runs inside the cluster (fortio)."""

    def p99_ms(name: str, url: str) -> float:
        out = subprocess.run(
            ["kubectl", "-n", NAMESPACE, "run", f"fortio-{name}-{uuid.uuid4().hex[:6]}", "--rm",
             "-i", "--restart=Never", "--quiet", f"--image={FORTIO_IMAGE}", "--", "load",
             "-quiet", "-c", "1", "-qps", "0", "-n", "500",
             "-H", "content-type: application/json", "-H", "anthropic-version: 2023-06-01",
             "-H", f"x-api-key: {CLIENT_KEY}", "-payload", body().decode(), url],
            capture_output=True, text=True, timeout=600,
        ).stdout  # fmt: skip
        [p99] = re.findall(r"# target 99% ([0-9.]+)", out)
        return float(p99) * 1000

    direct = p99_ms("direct", f"http://anthropic-a.{NAMESPACE}.svc.cluster.local:8080/v1/messages")
    via = p99_ms("gateway", IN_CLUSTER_URL + ANTHROPIC_PATH)
    print(f"\n{GATEWAY}: p99 direct {direct:.1f} ms, via gateway {via:.1f} ms")
    assert via - direct < 10, f"p99 overhead {via - direct:.1f} ms"


# --- S12–S13: usage accounting and budgets ----------------------------------------------------


def usage_records() -> list[dict]:
    """Recent per-request usage records, normalized to client / model / tokens / thread."""
    if USAGE_SOURCE == "envoy-k8s":
        logs = subprocess.run(
            ["kubectl", "-n", "envoy-gateway-system", "logs", "--since=3m", "--all-containers",
             "-l", "gateway.envoyproxy.io/owning-gateway-name=aigw-spike"],
            capture_output=True, text=True, timeout=60,
        ).stdout  # fmt: skip
        out = []
        for line in logs.splitlines():
            if not line.startswith("{"):
                continue
            r = json.loads(line)
            if "gen_ai.request.model" not in r:
                continue
            out.append({
                "client": r.get("client"), "model": r.get("gen_ai.request.model"),
                "input": r.get("gen_ai.usage.input_tokens"),
                "output": r.get("gen_ai.usage.output_tokens"),
                "cost": r.get("cost_microusd"), "thread": r.get("thread"), "raw": r,
            })  # fmt: skip
        return out
    pytest.fail(f"no usage record source configured for {GATEWAY}")


@pytest.mark.spec("FR-017", "NFR-005")
def test_s12_per_request_usage_record_without_content():
    marker = f"zebra-sapphire-{uuid.uuid4().hex[:8]}"
    thread = f"usage-{uuid.uuid4().hex[:8]}"
    assert post(content=body(text=marker), headers={THREAD: thread}).status_code == 200
    mine = []
    for _ in range(15):
        time.sleep(2)
        mine = [r for r in usage_records() if str(r["thread"]) == thread]
        if mine:
            break
    assert mine, "no usage record for the request"
    [record] = mine
    assert record["client"] == CLIENT_NAME, record["client"]
    assert record["model"] == "claude-sonnet-5-5"
    assert record["input"] not in (None, "", "-") and record["output"] not in (None, "", "-")
    assert int(record["output"]) == 3
    assert marker not in json.dumps(record["raw"]), "prompt content stored in the usage record"


@pytest.mark.spec("FR-018")
def test_s13_per_client_budget_enforced():
    assert BUDGET_KEY, "no budget key configured"
    statuses = []
    for _ in range(8):
        resp = post(headers={"x-api-key": BUDGET_KEY})
        statuses.append(resp.status_code)
        if resp.status_code in (400, 402, 429) or (
            resp.status_code == 422 and "budget_exceeded" in resp.text  # LiteLLM's code
        ):
            break
        time.sleep(2)
    assert statuses[0] == 200, statuses
    assert statuses[-1] in (400, 402, 422, 429), f"budget never enforced: {statuses}"


# --- S14–S17: discovery, placement constraint, self-hosted capacity, metrics ------------------


@pytest.mark.spec("FR-011")
def test_s14_model_discovery_and_unknown_model():
    listed = httpx.get(BASE + "/v1/models", headers=HEADERS, timeout=30)
    assert listed.status_code == 200
    ids = {m["id"] for m in listed.json()["data"]}
    assert {"claude-sonnet-5-5", "qwen-test"} <= ids, ids
    unknown = post(content=body(model="not-a-served-model"))
    assert unknown.status_code == 404, unknown.status_code
    assert received(ANTHROPIC) == []


@pytest.mark.spec("FR-012")
def test_s15_placement_constraint():
    for i in range(8):
        assert post(headers={"x-gateway-placement": "eu", THREAD: f"p{i}"}).status_code == 200
    assert {port for port, _ in received(ANTHROPIC)} == {9102}, "eu requests left the eu backend"


@pytest.mark.spec("FR-014")
def test_s16_self_hosted_capacity():
    """Each self-hosted backend admits GATEWAY_SELF_HOSTED_MAX in-flight requests (default 2
    per replica, two replicas); a burst beyond that gets 429 with retry-after, not a queue."""
    import concurrent.futures

    limit = int(os.environ.get("GATEWAY_SELF_HOSTED_MAX", "4"))
    slow = b'{"model": "qwen-test", "messages": [{"role": "user", "content": "SLOW-REQUEST"}]}'

    def call(_):
        headers = {"authorization": f"Bearer {CLIENT_KEY}", "x-api-key": CLIENT_KEY,
                   "content-type": "application/json"}  # fmt: skip
        return httpx.post(BASE + OPENAI_PATH, content=slow, timeout=30, headers=headers)

    with concurrent.futures.ThreadPoolExecutor(max_workers=limit + 4) as pool:
        resps = list(pool.map(call, range(limit + 4)))
    codes = sorted(r.status_code for r in resps)
    assert codes.count(200) >= limit, codes
    refused = [r for r in resps if r.status_code == 429]
    assert refused, f"no request refused beyond capacity: {codes}"
    assert all("retry-after" in {k.lower() for k in r.headers} for r in refused)


@pytest.mark.spec("FR-017")
def test_s17_genai_metrics_reach_the_collector():
    assert post().status_code == 200
    query = "http://localhost:3000/api/datasources/proxy/uid/prometheus/api/v1/query"
    found = {}
    deadline = time.time() + 120
    while time.time() < deadline and len(found) < 2:
        time.sleep(10)
        for metric in (
            "gen_ai_client_token_usage_sum",
            "gen_ai_server_request_duration_seconds_count",
        ):
            result = httpx.get(query, params={"query": metric}, timeout=10).json()
            if result.get("data", {}).get("result"):
                found[metric] = result["data"]["result"]
    assert len(found) == 2, f"metrics missing: {sorted(found)}"
    labels = [r["metric"] for r in found["gen_ai_client_token_usage_sum"]]
    assert {lbl.get("gen_ai_token_type") for lbl in labels} >= {"input", "output"}
