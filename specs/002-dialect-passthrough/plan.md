# Plan 002: Dialect pass-through, placement and capacity

Implements [spec.md](spec.md) within the ADR 0002 layout.

## Ports and adapters

| Layer | Piece | Responsibility | Requirements |
|---|---|---|---|
| domain | `Placement`, `ServedModel`, `TokenEstimate`, errors | value objects; no I/O | FR-011, FR-012 |
| application | `ForwardRequest` use case | auth → model lookup → deadline → placement → limiter → forward → usage correction → ledger | FR-009–FR-017 |
| application | ports: `ProviderClient`, `CapacityLimiter`, `ClientKeys`, `UsageLedger`, `PlacementHealth` | what the use case needs | |
| domain | `choose_placement(thread, constraint, placements, health)` | rendezvous hashing on `x-agent-thread`, weights otherwise; same model only | FR-012 |
| adapters/inbound/http | `/anthropic/v1/messages`, `/openai/v1/chat/completions`, `/v1/models` | read raw body bytes, map errors to each dialect's native shape, relay streams | FR-009–FR-011 |
| adapters/outbound | `AnthropicProvider` (Claude API, Claude Platform on AWS), `BedrockProvider`, `OpenAICompatibleProvider` (vLLM, SGLang) on httpx with an injectable transport | swap credentials, forward bytes, stream | FR-009, FR-010 |
| adapters/outbound | `InMemoryLimiter`, `RedisLimiter` | token bucket and concurrency; Redis for multi-replica | FR-013, FR-014 |
| adapters/outbound | `LogLedger` | structured ledger records | FR-017 |
| infrastructure | `GatewayConfig` (YAML file + env for secrets) | models, placements, weights, limits, client keys | FR-011–FR-015 |

## Key decisions
- **Raw bytes, not parsed-and-re-serialized bodies.** The inbound adapter keeps the request
  bytes and parses a copy only to read `model` and `stream`. Re-serializing could reorder or
  re-escape and breaks the byte-for-byte guarantee caching relies on.
- **Rendezvous hashing for affinity:** a thread keeps its placement, and when one placement
  is removed only its threads move.
- **Estimate then correct:** input tokens are estimated from body size before the call (to
  reject early) and the bucket is corrected from `usage` afterwards; cache reads refund.
- **Test seam:** `create_app(..., provider_transport=httpx.MockTransport(...))` lets tests
  run the real outbound adapters against a fake upstream offline.

## Risks
- The token estimate is coarse; repeated large over-estimates reject traffic Anthropic
  would accept. The correction step and a metric on estimate error bound this.
- Bedrock's request shape differs from the Messages API in places; `BedrockProvider` may
  need a Mantle-endpoint path. Verify before enabling a Bedrock placement.
