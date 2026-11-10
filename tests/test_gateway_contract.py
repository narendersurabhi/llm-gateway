"""FR-008: the gateway contract v0.1.0 carries ADR 0004's endpoints, headers and shapes."""

from pathlib import Path

import pytest
import yaml
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parent.parent
DOC = yaml.safe_load((ROOT / "contracts" / "openapi.yaml").read_text())
ANTHROPIC, OPENAI = "/anthropic/v1/messages", "/openai/v1/chat/completions"
AGENT_HEADERS = {"x-agent-thread", "x-agent-route", "x-agent-identity", "x-agent-deadline",
                 "x-gateway-placement"}  # fmt: skip


def resolve(node):
    while isinstance(node, dict) and "$ref" in node:
        target = DOC
        for part in node["$ref"][2:].split("/"):
            target = target[part]
        node = target
    return node


def schema(name: str) -> Draft202012Validator:
    return Draft202012Validator({"$ref": f"#/components/schemas/{name}",
                                 "components": DOC["components"]})  # fmt: skip


def header_names(op: dict) -> set[str]:
    return {resolve(p)["name"] for p in op.get("parameters", []) if resolve(p)["in"] == "header"}


@pytest.mark.spec("FR-008")
def test_endpoints_and_auth():
    assert DOC["info"]["version"] == "0.1.0"
    paths = DOC["paths"]
    assert set(paths) == {"/healthz", ANTHROPIC, OPENAI, "/v1/models"}
    assert paths[ANTHROPIC]["post"]["security"] == [{"gatewayKey": []}]
    assert paths[OPENAI]["post"]["security"] == [{"gatewayBearer": []}]
    assert paths["/healthz"]["get"]["security"] == []
    schemes = DOC["components"]["securitySchemes"]
    assert schemes["gatewayKey"] == {**schemes["gatewayKey"], "in": "header", "name": "x-api-key"}
    assert schemes["gatewayBearer"]["scheme"] == "bearer"


@pytest.mark.spec("FR-008")
@pytest.mark.parametrize("path", [ANTHROPIC, OPENAI])
def test_dialect_endpoints_take_agent_headers_and_stream(path):
    op = DOC["paths"][path]["post"]
    assert header_names(op) >= AGENT_HEADERS
    route = next(resolve(p) for p in op["parameters"] if resolve(p)["name"] == "x-agent-route")
    assert route["schema"]["enum"] == ["interactive", "background"]
    ok = op["responses"]["200"]
    assert set(ok["content"]) == {"application/json", "text/event-stream"}
    assert "x-gateway-provider" in ok["headers"]
    limited = resolve(op["responses"]["429"])
    assert "retry-after" in limited["headers"]


@pytest.mark.spec("FR-008")
def test_bodies_pass_through_unknown_fields():
    anthropic = schema("AnthropicMessagesRequest")
    body = {"model": "claude-sonnet-5-5", "max_tokens": 1024,
            "messages": [{"role": "user", "content": "hi"}],
            "system": [{"type": "text", "text": "s", "cache_control": {"type": "ephemeral"}}],
            "tools": [{"name": "t", "input_schema": {"type": "object"}}],
            "cache_control": {"type": "ephemeral"}, "thinking": {"type": "adaptive"}}  # fmt: skip
    anthropic.validate(body)
    assert not anthropic.is_valid({k: v for k, v in body.items() if k != "max_tokens"})
    openai = schema("ChatCompletionRequest")
    openai.validate({"model": "qwen", "messages": [{"role": "user", "content": "hi"}],
                     "tools": [], "temperature": 0.2})  # fmt: skip
    assert "anthropic-version" in header_names(DOC["paths"][ANTHROPIC]["post"])


@pytest.mark.spec("FR-008")
def test_usage_cache_fields_and_native_error_shapes():
    usage = DOC["components"]["schemas"]["AnthropicMessage"]["properties"]["usage"]["properties"]
    assert {"cache_read_input_tokens", "cache_creation_input_tokens"} <= set(usage)
    schema("AnthropicError").validate(
        {"type": "error", "error": {"type": "rate_limit_error", "message": "slow down"}}
    )
    schema("OpenAIError").validate(
        {"error": {"message": "busy", "type": "rate_limit", "code": None}}
    )


@pytest.mark.spec("FR-008")
def test_model_discovery_shape():
    models = schema("ModelList")
    models.validate({"data": [
        {"id": "claude-sonnet-5-5", "dialect": "anthropic",
         "capabilities": {"tool_calling": True, "max_input_tokens": 1_000_000,
                          "prompt_caching": "explicit", "thinking": True}},
        {"id": "<qwen-model-id>", "dialect": "openai",
         "capabilities": {"tool_calling": True, "max_input_tokens": 131072,
                          "prompt_caching": "automatic"}},
    ]})  # fmt: skip
    assert not models.is_valid({"data": [{"id": "x", "dialect": "gemini", "capabilities": {
        "tool_calling": True, "max_input_tokens": 1, "prompt_caching": "none"}}]})  # fmt: skip
