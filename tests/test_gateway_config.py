"""Spec 002: offline checks on the rendered gateway configuration.

They read `kubectl kustomize deploy/gateway/overlays/<overlay>`, i.e. exactly what would be
applied, and fail until `deploy/` exists: `xfail(strict=True)` keeps the suite green and fails
it as soon as a check starts passing, so each marker is removed as its requirement lands.
"""

import re
import subprocess
from functools import cache
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.xfail(strict=True, reason="spec 002: deploy/ not written yet")

ROOT = Path(__file__).resolve().parent.parent
DEPLOY = ROOT / "deploy"


@cache
def rendered(overlay: str) -> tuple[dict, ...]:
    out = subprocess.run(["kubectl", "kustomize", str(DEPLOY / "gateway" / "overlays" / overlay)],
                         capture_output=True, text=True, check=True).stdout  # fmt: skip
    return tuple(d for d in yaml.safe_load_all(out) if d)


def of_kind(docs, kind: str) -> list[dict]:
    return [d for d in docs if d["kind"] == kind]


def route(docs) -> dict:
    [r] = of_kind(docs, "AIGatewayRoute")
    return r


def traffic_policy(docs) -> dict:
    [p] = [p for p in of_kind(docs, "BackendTrafficPolicy") if "rateLimit" in p["spec"]]
    return p


@pytest.mark.spec("FR-009")
@pytest.mark.parametrize("overlay", ["kind", "eks"])
def test_each_anthropic_backend_has_its_own_key_policy(overlay):
    docs = rendered(overlay)
    anthropic = {b["metadata"]["name"] for b in of_kind(docs, "AIServiceBackend")
                 if b["spec"]["schema"]["name"] == "Anthropic"}  # fmt: skip
    assert anthropic
    policies = [
        p for p in of_kind(docs, "BackendSecurityPolicy") if p["spec"]["type"] == "AnthropicAPIKey"
    ]
    targets = [t["name"] for p in policies for t in p["spec"]["targetRefs"]]
    assert sorted(targets) == sorted(anthropic), (
        "every Anthropic backend needs exactly one key policy"
    )
    secrets = [p["spec"]["anthropicAPIKey"]["secretRef"]["name"] for p in policies]
    assert len(set(secrets)) == len(secrets), "backends share a credential secret"


@pytest.mark.spec("FR-012")
def test_affinity_and_same_model_retries():
    spec = traffic_policy(rendered("kind"))["spec"]
    affinity = {"type": "Header", "header": {"name": "x-agent-thread"}}
    assert spec["loadBalancer"] == {"type": "ConsistentHash", "consistentHash": affinity}
    assert spec["retry"]["numAttemptsPerPriority"] == 1
    assert set(spec["retry"]["retryOn"]["httpStatusCodes"]) >= {500, 502, 503, 529}


@pytest.mark.spec("FR-013", "FR-018")
def test_costs_and_rate_limits():
    costs = {c["metadataKey"]: c for c in route(rendered("kind"))["spec"]["llmRequestCosts"]}
    assert (
        costs["llm_counted_input_token"]["cel"].replace(" ", "")
        == "input_tokens-cached_input_tokens"
    )
    assert "llm_cost_microusd" in costs
    rules = traffic_policy(rendered("kind"))["spec"]["rateLimit"]["global"]["rules"]
    keys = {r["cost"]["response"]["metadata"]["key"] for r in rules}
    assert {"llm_counted_input_token", "llm_cost_microusd"} <= keys


@pytest.mark.spec("FR-013")
def test_rate_limited_responses_use_the_anthropic_shape():
    overrides = traffic_policy(rendered("kind"))["spec"]["responseOverride"]
    [o] = [o for o in overrides if {"type": "Value", "value": 429} in o["match"]["statusCodes"]]
    assert '"rate_limit_error"' in o["response"]["body"]["inline"]
    assert {h["name"] for h in o["response"]["header"]["set"]} == {"retry-after"}


@pytest.mark.spec("FR-014")
def test_self_hosted_backends_have_a_circuit_breaker():
    docs = rendered("kind")
    self_hosted = {b["metadata"]["name"] for b in of_kind(docs, "AIServiceBackend")
                   if b["spec"]["schema"]["name"] == "OpenAI"}  # fmt: skip
    policies = [p for p in of_kind(docs, "BackendTrafficPolicy") if "circuitBreaker" in p["spec"]]
    breakers = {t["name"]: p["spec"]["circuitBreaker"] for p in policies
                for t in p["spec"]["targetRefs"]}  # fmt: skip
    for name in self_hosted:
        assert breakers.get(name, {}).get("maxParallelRequests"), name


@pytest.mark.spec("FR-015")
@pytest.mark.parametrize("overlay", ["kind", "eks"])
def test_client_auth(overlay):
    docs = rendered(overlay)
    [policy] = of_kind(docs, "SecurityPolicy")
    auth = policy["spec"]["apiKeyAuth"]
    assert auth["sanitize"] is True and auth["forwardClientIDHeader"] == "x-client-id"
    if overlay == "eks":
        for secret in of_kind(docs, "Secret"):
            assert not secret.get("stringData") and not secret.get("data"), (
                f"literal secret {secret['metadata']['name']} in the EKS overlay"
            )


@pytest.mark.spec("FR-017", "NFR-005")
def test_genai_tracing_without_content():
    values = (DEPLOY / "install" / "ai-gateway-values.yaml").read_text()
    env = {e["name"]: e["value"] for e in yaml.safe_load(values)["extProc"]["extraEnvVars"]}
    assert env["AI_GATEWAY_TRACING_SEMCONV"] == "gen_ai"
    assert env.get("OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT", "false") == "false"


@pytest.mark.spec("FR-017")
def test_usage_access_log_fields():
    [proxy] = of_kind(rendered("kind"), "EnvoyProxy")
    [setting] = proxy["spec"]["telemetry"]["accessLog"]["settings"]
    fields = setting["format"]["json"]
    assert {"client", "thread", "gen_ai.request.model", "gen_ai.usage.input_tokens",
            "gen_ai.usage.cached_input_tokens", "gen_ai.usage.output_tokens",
            "cost_microusd"} <= set(fields)  # fmt: skip
    assert not any("BODY" in str(v).upper() for v in fields.values()), "bodies in access logs"


@pytest.mark.spec("FR-019")
def test_validator_rejects_bad_cel_and_accepts_the_config(tmp_path):
    validate = DEPLOY / "validate.sh"
    assert subprocess.run([str(validate), "kind"], capture_output=True).returncode == 0
    bad = tmp_path / "bad"
    bad.mkdir()
    (bad / "kustomization.yaml").write_text(
        "resources:\n- " + str(DEPLOY / "gateway" / "overlays" / "kind") + "\n"
        "patches:\n- target: {kind: AIGatewayRoute}\n  patch: |-\n"
        "    - op: add\n      path: /spec/llmRequestCosts/-\n"
        '      value: {metadataKey: bad, type: CEL, cel: "input_tokens * 2"}\n'
    )
    proc = subprocess.run([str(validate), str(bad)], capture_output=True, text=True)
    assert proc.returncode != 0 and "cel" in (proc.stdout + proc.stderr).lower()


@pytest.mark.spec("FR-020", "NFR-007")
def test_versions_pinned_and_acceptance_report_matches():
    versions = (DEPLOY / "versions.env").read_text()
    pins = dict(line.split("=", 1) for line in versions.splitlines() if "=" in line)
    for name in ("ENVOY_GATEWAY_VERSION", "AI_GATEWAY_VERSION", "REDIS_IMAGE", "WIREMOCK_IMAGE",
                 "KIND_NODE_IMAGE"):  # fmt: skip
        assert pins.get(name) and not re.search(r"latest|main", pins[name]), name
    report = (DEPLOY / "kind" / "acceptance-report.txt").read_text()
    for value in pins.values():
        assert value in report, f"acceptance report does not cover {value}"
    assert re.search(r"\b\d+ passed\b", report) and "failed" not in report


@pytest.mark.spec("FR-021")
def test_contract_v1_matches_envoy():
    doc = yaml.safe_load((ROOT / "contracts" / "openapi.yaml").read_text())
    assert int(doc["info"]["version"].split(".")[0]) >= 1
    assert {"/anthropic/v1/messages", "/v1/chat/completions", "/v1/models"} <= set(doc["paths"])
    assert "/openai/v1/chat/completions" not in doc["paths"]
    served = doc["components"]["schemas"].get("ServedModel", {})
    assert "capabilities" not in served.get("properties", {})
    assert "x-gateway-provider" not in yaml.safe_dump(doc)
