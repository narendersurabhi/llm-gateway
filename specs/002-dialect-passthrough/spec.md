# Spec 002: Dialect pass-through, placement and capacity

| | |
|---|---|
| **Status** | Draft: tests written (`xfail`), not implemented |
| **Created** | 2026-11-10 |
| **Plan** | [plan.md](plan.md) · **Tasks** [tasks.md](tasks.md) |
| **Decisions** | agent-platform ADR 0004 (dialects, model ownership, caching, limiters) |

## Summary
Serve contract v0.1.0: forward Anthropic and OpenAI-compatible requests natively to the
model's placements, without ever changing the model; keep a conversation on one placement
so prompt caches stay warm; enforce provider quotas and self-hosted capacity; hold all
provider credentials; and account for every token without recording any content.

## Out of scope
Format conversion between dialects (rejected in ADR 0004). Response or semantic caching.
Hosting models (a future `model-serving` repo). Choosing a model for the caller.

---

## Functional requirements

### FR-009: Anthropic pass-through
- **Given** a valid `POST /anthropic/v1/messages`, **when** it is forwarded, **then** the
  provider receives the request body byte for byte (system blocks, tool order, history and
  every `cache_control` marker unchanged), the gateway client key is replaced by the
  placement's credential, and `anthropic-version` / `anthropic-beta` are passed on.
- The provider's status, body and `usage` come back unchanged, plus `x-gateway-provider`.
- **Given** `"stream": true`, **then** SSE events are relayed in order as they arrive, not
  buffered.

### FR-010: OpenAI-compatible pass-through
- Same as FR-009 for `POST /openai/v1/chat/completions` to self-hosted replicas (vLLM,
  SGLang): body unchanged, bearer replaced, response and stream relayed unchanged.

### FR-011: Model discovery and dialect checks
- `GET /v1/models` lists exactly the configured models with dialect and capabilities.
- **Given** a `model` that is not served, **then** `404` in the endpoint's native error
  shape; **given** a served model on the other dialect's endpoint, **then** `400`.

### FR-012: Placement
- Configuration maps each model to placements (provider, weight, and workspace or replica
  endpoints). The gateway never changes the request's `model`.
- **Given** the same `x-agent-thread`, **then** requests go to the same placement (and
  workspace or replica) while it is healthy; without the header, by weight.
- **Given** `x-gateway-placement`, **then** only matching placements are used; if none
  match, `400`.
- **Given** a placement fails (connection error, `5xx`, `529`), **then** the request may
  fail over only to another placement of the **same** model, and the failover is counted.

### FR-013: Quota limiter (provider placements)
- A token bucket per placement for requests, input tokens and output tokens. Input counts
  only uncached input plus cache writes: estimated before the call, corrected from `usage`.
- **Given** a bucket is exhausted, **then** `429` with `retry-after` in the native error
  shape; `background` requests are refused before `interactive` ones as a bucket nears
  empty.
- Limiter state is shared across gateway replicas.

### FR-014: Concurrency limiter (self-hosted placements)
- At most N in-flight requests per replica (configured). **Given** all replicas of a model
  are full, **then** `429` with `retry-after`.

### FR-015: Client authentication
- Gateway client keys map to a calling-service name, compared in constant time.
  **Given** a missing or unknown key, **then** `401` in the native error shape and nothing
  is forwarded. Provider credentials exist only in the gateway's configuration and secrets.

### FR-016: Deadlines
- **Given** `x-agent-deadline` has passed, **then** `504` without calling the provider;
  otherwise the upstream timeout is the remaining time.

## Observability

### FR-017: Spans, usage and metrics
- One `chat <model>` CLIENT span per request with `gen_ai.provider.name`,
  `gen_ai.request.model`, `gen_ai.response.model`, `gen_ai.usage.input_tokens`,
  `gen_ai.usage.output_tokens`, cache read and cache creation tokens, the placement, the
  calling service and failover count. Errors set ERROR status with the exception type only.
- Metrics (bounded attributes: provider, model from the served list, route class,
  calling service, outcome): `gen_ai.client.operation.duration`, `gen_ai.client.token.usage`
  with `gen_ai.token.type` in `input` / `output` / `cache_read` / `cache_creation`,
  time to first chunk for streams, limiter rejections, failovers.
- One usage-ledger log record per request (calling service, tenant from
  `x-agent-identity`, model, placement, token counts), for cost accounting.

## Non-functional requirements

### NFR-005: No content in telemetry
- Prompts, responses, tool definitions and API keys never appear in any signal.

### NFR-006: Low overhead
- With a local fake upstream, the gateway adds under 10 ms at p99 to a non-streamed request
  and under 5 ms to the first streamed chunk.
