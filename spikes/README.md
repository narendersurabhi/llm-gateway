# Gateway framework spike (2026-11-12)

**Outcome:** Envoy AI Gateway 1.2.0 on Kubernetes passes all 12 checks with the config in
[k8s/gateway.yaml](k8s/gateway.yaml). Standalone `aigw run` and LiteLLM 1.106.6 do not.

Spec 002's must-haves as black-box checks ([test_acceptance.py](test_acceptance.py)) against
off-the-shelf gateways, before writing any gateway code. WireMock stands in for the providers
(two Anthropic "workspaces", two vLLM replicas); otel-lgtm collects traces.

```bash
docker compose up -d
./run_spike.sh litellm      # LiteLLM 1.106.6 via uvx, config in litellm/
./run_spike.sh envoy        # Envoy AI Gateway 1.2.0 `aigw run` (binary in bin/, see below)
docker compose down
```

`bin/aigw` is the `aigw-darwin-arm64` asset of release v1.2.0, checked against the SHA-256
digest GitHub records for it: `4be4a8fdfee58acc52aba1a9e71e598df00a17ad05477cb3f4e706604636df3e`.

## Kubernetes run (Envoy AI Gateway 1.2.0 on Envoy Gateway 1.9.3, kind 0.33.0, Kubernetes 1.37)

```bash
k8s/setup.sh                                   # kind cluster, Redis, Envoy Gateway + AI Gateway (pinned)
kubectl apply -f k8s/upstreams.yaml -f k8s/gateway.yaml
# port-forward the Envoy service to :1975, WireMock to :9101-9104, otel-lgtm to :3000, then:
SPIKE_GATEWAY=envoy-k8s SPIKE_BASE_URL=http://localhost:1975 SPIKE_ANTHROPIC_PATH=/anthropic/v1/messages \
  SPIKE_OTEL_SERVICE=envoy-k8s-spike uv run pytest spikes -m live
bin/kind delete cluster --name aigw-spike
```

| Check | Result on Kubernetes | Config that does it |
|---|---|---|
| S1–S3 fidelity (bytes, usage, SSE) | ✓ | `schema: {name: Anthropic}` |
| S4 per-backend provider key | ✓ 15/15 per workspace (the standalone mix-up does not reproduce) | `BackendSecurityPolicy` `AnthropicAPIKey` per `AIServiceBackend` |
| S5 client key required | ✓ 401 | `SecurityPolicy.apiKeyAuth` with `sanitize: true` |
| S6 OpenAI-compatible | ✓ | `schema: {name: OpenAI}` |
| S7 thread affinity | ✓ | `BackendTrafficPolicy.loadBalancer.consistentHash.header: x-agent-thread` |
| S8 failover, same model | ✓ | `retry.numAttemptsPerPriority: 1`, `retriable-status-codes [500, 502, 503, 529]` |
| S9 cache-aware token limit | ✓ cached calls pass, uncached overrun gets 429 | `llmRequestCosts` CEL `input_tokens - cached_input_tokens` + global rate limit (Redis) |
| S9 429 shape | ✓ Anthropic error body, `retry-after` | `responseOverride` for local 429s; `retry-after` is static (header operators like `%RESP(...)%` are not evaluated) |
| S10 no prompt content in traces | ✓ | extProc env `AI_GATEWAY_TRACING_SEMCONV=gen_ai` |
| S11 overhead | ✓ in-cluster (fortio 1.76.0, 500 calls, 1 connection): +1.7 ms p50, +4.3 ms p99 | — |

Still to check before production: whether Envoy's `input_tokens` for Anthropic includes cache
*writes* (Anthropic counts them against input limits; the stubs had none), a 401 body in the
Anthropic error shape (another `responseOverride`), llm-d for self-hosted routing, and load at
production rates.

## Standalone and LiteLLM run

| Check | LiteLLM 1.106.6 | Envoy AI Gateway 1.2.0 (`aigw run`, standalone) |
|---|---|---|
| S1 body forwarded byte for byte | ✗ re-serialized; adds `"stream": false` | ✓ |
| S1b body semantically unchanged (every `cache_control`, 1h TTL, tool order) | ✓ | ✓ |
| S2 response and `usage` (incl. cache fields) unchanged | ✓ | ✓ |
| S3 SSE stream relayed unchanged | ✓ | ✓ |
| S4 provider key injected, client key not forwarded | ✓ | ✗ **the second backend in a route gets the first backend's key** (30/30 requests, with priorities and with weights) |
| S5 client key required (401) | ✗ returns **500** (`No api key passed in.`), which SDKs retry | ✗ untestable standalone: `apiKeyAuth` makes every request 500 |
| S6 OpenAI-compatible to vLLM | ✓ (adds `"stream": false`) | ✓ |
| S7 thread affinity | ✓ `optional_pre_call_checks: ["session_affinity"]` + `x-litellm-session-id` | ✗ standalone: a `consistentHash` header policy stops Envoy from starting |
| S8 failover keeps the model | ✗ 1 of 8 requests got 500 during failover | ✓ |
| S9 cache-aware token limit | ✗ model `tpm` only steers routing; hard limits need DB-backed virtual keys (cache-awareness unknown) | ✗ standalone: global rate limiting needs Redis and Envoy Gateway rate-limit config (stops startup); the CEL cost `input_tokens - cached_input_tokens` is accepted |
| S10 no prompt content in traces | ✓ `turn_off_message_logging: true` | ✓ `AI_GATEWAY_TRACING_SEMCONV=gen_ai` (the default OpenInference convention records content) |
| S11 added latency | p50 ~8–10 ms, p99 ~10 ms | p50 ~1.3 ms |
| **Passed** | **7 / 12** | **8 / 12** (3 failures are standalone-mode limits) |

## Observations

- **Envoy's data path is the best fit:** byte-exact forwarding, ~1 ms overhead, clean
  failover and trace hygiene. Its gaps here are Envoy Gateway features (rate limiting,
  consistent hashing, API-key auth) that `aigw run` doesn't support, plus one serious bug
  (S4) that must be re-checked on Kubernetes before relying on multiple keyed backends per
  route.
- **LiteLLM works end to end in one process** but changes request bodies, mis-reports auth
  failures as 500, failed one request during failover, needs a database for hard limits,
  and adds ~8× Envoy's latency.
- The Envoy AI Gateway repository now lives at `theagentrouter/agent-router` (formerly
  `envoyproxy/ai-gateway`) and its docs at theagentrouter.ai. Confirm the project's
  governance before depending on it.
- No existing upstream issues were found for the S4, S5 and S7 behaviour in either project.

## Not covered

Kubernetes deployment of Envoy AI Gateway (where S5, S7, S9 should be configurable), llm-d
for self-hosted routing, LiteLLM with its database (virtual-key limits), and load at
production rates.
