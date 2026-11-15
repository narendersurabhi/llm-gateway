# Spec 002: The gateway on Envoy AI Gateway

| | |
|---|---|
| **Status** | Draft: tests written, not implemented |
| **Created** | 2026-11-14 |
| **Plan** | [plan.md](plan.md) · **Tasks** [tasks.md](tasks.md) |
| **Decisions** | agent-platform ADR 0004 (what the gateway must do), ADR 0006 (Envoy AI Gateway on Kubernetes) |
| **Evidence** | the framework comparison on branch `chore/gateway-framework-spike` (`spikes/README.md`) |
| **Supersedes** | the custom-build draft on `spec/002-dialect-passthrough` (same IDs for the same behaviour; its code is stashed, unused) |

## Summary
Serve the gateway contract with **Envoy AI Gateway on Envoy Gateway, on Kubernetes**, rather
than our own code. This repo holds the pinned installation, the gateway configuration (Gateway,
routes, backends, security, traffic and telemetry policies) with kind and EKS overlays, offline
validation of that configuration in the hooks, and the black-box acceptance suite that every
configuration change and every framework upgrade must pass.

## Out of scope
Cumulative per-tenant spend and budgets (spec 003, a ledger fed by this spec's access logs).
The EKS cluster, VPC and Redis themselves (agent-platform spec 002). Self-hosted model serving
and llm-d. Converting between dialects (ADR 0004).

## How requirements are verified
Two layers, both under `tests/`:
- **Acceptance** (`tests/acceptance/`, marked `live`): black-box checks against a running
  gateway with WireMock upstreams, run on a pinned kind cluster by `deploy/kind/acceptance.sh`.
  They are the spike's 14 checks plus new ones for discovery, placement constraints and
  self-hosted capacity.
- **Configuration** (`tests/test_gateway_config.py`, default suite): offline checks on the
  rendered manifests. They are `xfail(strict=True)` until `deploy/` exists.

---

## Functional requirements

### FR-009: Anthropic pass-through
- **Given** a valid `POST /anthropic/v1/messages`, **when** it is forwarded, **then** the
  provider receives the body byte for byte (system blocks, tool order, history, every
  `cache_control` including TTLs), with the gateway client key replaced by the backend's own
  credential; the provider's status, body and `usage` come back unchanged; with
  `"stream": true` the SSE events are relayed unchanged.
- Every Anthropic backend is an `AIServiceBackend` with `schema: {name: Anthropic}` and its own
  `BackendSecurityPolicy` of type `AnthropicAPIKey`.

### FR-010: OpenAI-compatible pass-through
- **Given** a valid `POST /v1/chat/completions` for a self-hosted model, **then** the body
  reaches the replica unchanged and the response is relayed unchanged.

### FR-011: Model discovery
- `GET /v1/models` lists exactly the served model IDs. **Given** a request for a model that is
  not served, **then** the gateway answers `404` and no upstream is called. (Dialect and
  capabilities live in the agent config, ADR 0006.)

### FR-012: Placement, affinity and failover
- Requests carrying the same `x-agent-thread` go to the same backend while it is healthy;
  different threads spread across a model's backends.
- **Given** `x-gateway-placement: eu`, **then** only backends labelled for `eu` are used.
- **Given** a backend fails (connection error, `500`, `502`, `503`, `529`), **then** the request
  is retried once on another backend of the **same** model, and the model is never changed.

### FR-013: Cache-aware token quotas
- Input quotas count uncached input only (`input_tokens - cached_input_tokens` in
  `llmRequestCosts`, ADR 0004). Five calls whose input is mostly cache reads stay under a
  2000-token-per-minute limit; a call that exceeds it with uncached input gets the next call
  refused.
- A refusal is `429` with the Anthropic error body (`rate_limit_error`) and a `retry-after`
  header. Envoy cannot compute it per request, so the value is a configured constant.
- Open question for the implementation: whether Envoy's `input_tokens` for Anthropic includes
  cache *writes*, which Anthropic also counts. The acceptance fixtures add a cache-write case.

### FR-014: Self-hosted capacity
- At most N requests are in flight per self-hosted backend (an Envoy circuit breaker). Excess
  requests get `429` with `retry-after` rather than queueing indefinitely.

### FR-015: Client authentication
- **Given** a missing or unknown gateway key, **then** `401` in the Anthropic error shape and
  nothing is forwarded. Keys come from Kubernetes Secrets (never literal values in the EKS
  overlay); the key header is removed before forwarding (`sanitize`), and the client's name is
  forwarded as `x-client-id` for logging and per-client limits.

### ~~FR-016~~ (withdrawn: Envoy has no per-request deadline from a header; callers enforce `x-agent-deadline` by cancelling the request, which Envoy propagates upstream, and routes carry a fixed maximum timeout)

### FR-017: Telemetry
- Spans follow the OpenTelemetry GenAI conventions (`AI_GATEWAY_TRACING_SEMCONV=gen_ai`), with
  message-content capture left off.
- Metrics `gen_ai.client.token.usage` (by `gen_ai.token.type`) and
  `gen_ai.server.request.duration` reach the collector.
- One JSON access-log line per request carries the client, `x-agent-thread`, model, input,
  cached-input and output tokens, a cost estimate, status and duration, and no content. This
  is the input to the spec 003 ledger.

### FR-018: Per-client budgets per window
- A cost-weighted rate limit per client (cost from `llmRequestCosts` in micro-USD) refuses a
  client's requests once its window's budget is spent. This is a per-window budget;
  cumulative budgets are spec 003.

### FR-019: Configuration validated before it is applied
- The commit hook rejects manifests that do not match the installed CRD schemas and any
  `llmRequestCosts` CEL expression that does not compile. During the spike, an expression
  multiplying a `uint` by an `int` literal was accepted by the API server and then made every
  request fail.
- The tool for each check is chosen by evaluating existing validators first (user rule:
  off-the-shelf before custom code).

### FR-020: Pinned, reproducible installation
- Every version is pinned in one file: Envoy Gateway, AI Gateway charts and images, Redis,
  WireMock, kind node image. Helm values are fetched at the release tag, never `main`.
- `deploy/kind/acceptance.sh` builds a fresh kind cluster, installs the gateway with the kind
  overlay and runs the acceptance suite. An EKS overlay differs only in backends, secrets,
  load balancer and replicas.

### FR-021: Contract v1.0.0
- `contracts/openapi.yaml` describes what Envoy serves:
  - `/anthropic/v1/messages`, `/v1/chat/completions`, and `/v1/models` with model IDs only;
  - the agent headers;
  - Anthropic-shaped `401` and `429` responses.
- The `ServedModel` capability fields and the `x-gateway-provider` header are removed. That is
  a breaking change, so the MAJOR version bumps.

## Non-functional requirements

### NFR-005: No conversation content in telemetry
- Prompts, responses and keys appear in no span, log line or metric. The default OpenInference
  convention records content, so the configuration must select `gen_ai` and must not set
  `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=true`.

### NFR-006: Low overhead
- Measured inside the cluster with a load tester, the gateway adds under 10 ms at p99 for a
  single connection (the spike measured +1.5 ms p50, +4.3 ms p99).

### NFR-007: Upgrades are gated
- A change to any pinned version or gateway manifest merges only with a green acceptance run
  recorded in the PR (the acceptance report file is committed with the change).
