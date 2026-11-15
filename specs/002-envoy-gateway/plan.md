# Plan 002: The gateway on Envoy AI Gateway

Implements [spec.md](spec.md). Checked against [constitution](../constitution.md) and
agent-platform ADR 0006.

## Layout

```
deploy/
  versions.env                 every pinned version (FR-020)
  install/                     Helm installs of Envoy Gateway (+ rate-limit addon) and AI Gateway
  gateway/
    base/                      Gateway, EnvoyProxy (access log), AIGatewayRoute, BackendTrafficPolicy
                               (affinity, retries, rate limits, responseOverride), SecurityPolicy
    overlays/kind/             WireMock backends, literal test keys, upstreams, otel-lgtm
    overlays/eks/              real backends, keys from Secrets, NLB service, replicas
  kind/acceptance.sh           fresh cluster -> install -> apply kind overlay -> tests/acceptance
tests/
  acceptance/                  black-box checks (live), WireMock fixtures
  test_gateway_config.py       offline checks on rendered manifests (default suite)
src/llm_gateway/               unchanged here; spec 003's ledger lives here (ports and adapters)
```

Kustomize renders `base` plus an overlay. The offline tests and hooks read the rendered output,
so they see exactly what would be applied.

## Requirement → mechanism → check

| Req | Envoy mechanism | Acceptance check | Offline check |
|---|---|---|---|
| FR-009 | `schema: Anthropic`, `AnthropicAPIKey` per backend | S1, S1b, S2, S3, S4 | every Anthropic backend has its own key policy |
| FR-010 | `schema: OpenAI` backend | S6 | — |
| FR-011 | AI Gateway's `/v1/models`, route-not-found response | S14 | — |
| FR-012 | `consistentHash.header: x-agent-thread`; header-matched rule for `x-gateway-placement`; `retry.numAttemptsPerPriority: 1` | S7, S8, S15 | consistent hash on `x-agent-thread`; retry codes |
| FR-013 | CEL cost `input_tokens - cached_input_tokens`, global rate limit (Redis), `responseOverride` 429 | S9 | CEL compiles; 429 override present |
| FR-014 | `circuitBreaker.maxParallelRequests` on self-hosted backends, 503 → 429 override | S16 | circuit breaker set |
| FR-015 | `SecurityPolicy.apiKeyAuth`, `sanitize: true`, `forwardClientIDHeader: x-client-id` | S4, S5 | sanitize on; no literal keys in eks overlay |
| FR-017 | extProc env, `EnvoyProxy` JSON access log | S10, S12, S17 | semconv `gen_ai`; access-log fields |
| FR-018 | cost-weighted rate limit per `x-client-id` | S13 | — |
| FR-019 | — | — | manifests validate against CRDs; CEL compiles |
| FR-020 | `versions.env`, Helm values at tag | the acceptance run itself | no `main`/`latest` refs |
| FR-021 | `contracts/openapi.yaml` 1.0.0 | — | contract tests (spec 001 FR-008 updated) |
| NFR-005 | semconv `gen_ai`, content capture off | S10, S12 | env check |
| NFR-006 | — | S11 (fortio in-cluster) | — |
| NFR-007 | PR template + committed report | — | report present for version changes |

## Tools to evaluate before writing anything (FR-019)
- **Manifest schemas:** `kubeconform` with the CRD schemas extracted from the pinned charts;
  alternatively a server-side `kubectl apply --dry-run=server` against the kind cluster.
- **CEL:** the `cel-go` toolchain (the evaluator AI Gateway uses) versus `cel-python`. The
  validator must reject `uint * int`, the spike's failure, so a dynamic evaluator that
  allows it doesn't qualify. If neither works offline, run the server-side dry run in the
  hook against a local kind cluster.

## Key decisions
- **The acceptance suite runs on kind, not in the default suite.** It needs a cluster;
  default tests stay offline (constitution VII). NFR-007 makes it mandatory for
  infrastructure changes instead.
- **No Python on the request path.** `src/llm_gateway` keeps the template skeleton for spec
  003's ledger; nothing in this spec runs Python per request.
- **`retry-after` is a constant** (Envoy evaluates no operators in override headers); the SDKs
  also honour `x-ratelimit-reset`, which Envoy sends on non-overridden 429s.

## Risks
- Upstream project change of home (`theagentrouter/agent-router`); pinned versions plus the
  acceptance gate bound it.
- The standalone credential issue (private advisory) is not known to affect Kubernetes; the
  acceptance check S4 guards it on every run.
- Laptop kind numbers are not production capacity; load at production rates is part of the
  EKS rollout, not this spec.
