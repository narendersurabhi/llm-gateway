# Spec 001: Service skeleton and contract v0.1.0

| | |
|---|---|
| **Status** | Implemented (skeleton from the agent-platform service template; FR-008 added here) |
| **Plan** | [plan.md](plan.md) · **Tasks** [tasks.md](tasks.md) |

## Summary
The hexagonal skeleton every agent service starts from: a health endpoint, the shared error
envelope, request telemetry, the layer rule, a versioned provided contract and a container
image. Service features build on it in later specs, with IDs continuing from the highest
used here.

## Out of scope
Any service behaviour beyond health. Serving the contract's gateway endpoints is spec 002;
this spec publishes the contract so consumers can pin it and build against it now.

---

## Functional requirements

### FR-001: Health endpoint
- **Given** the service is running, **when** `GET /healthz` is called without credentials,
  **then** it returns `200` `{"status": "ok", "version": "<package version>", "uptime_s": <n>}`.

### FR-002: Error envelope
- **Given** a request fails, **when** the response is sent, **then** it is the conventions'
  envelope `{"error": {"type", "message"}, "trace_id"}` with the status for that `type`.
- Domain errors map by `type`; schema validation failures are `422 invalid_request` naming
  the fields but not echoing their values; unknown routes are `404 not_found`; unhandled
  exceptions are `500 internal` with no exception text; `429` and `503` carry `retry-after`.

## Observability

### FR-003: Request telemetry
- **Given** a request (other than `/healthz`), **when** it completes, **then** there is an
  HTTP server span named for the route template and an `http.server.request.duration`
  metric with bounded attributes, and logs emitted while handling it are JSON lines carrying
  its `trace_id` and `span_id` (and are exported through the OpenTelemetry logs pipeline).
- `/healthz` produces no spans.

### FR-004: Telemetry configuration and lifecycle
- Exporters `none` / `console` / `otlp` per signal from `OTEL_TRACES_EXPORTER`,
  `OTEL_LOGS_EXPORTER`, `OTEL_METRICS_EXPORTER` (default `none`); the OTLP endpoint from
  `OTEL_EXPORTER_OTLP_ENDPOINT`; `service.name` from `OTEL_SERVICE_NAME` (default
  `llm-gateway`); `deployment.environment.name` from `LLM_GATEWAY_ENVIRONMENT`.
  Invalid values fail startup.
- **Given** the service shuts down, **then** buffered telemetry is flushed and the
  providers are shut down.

## Architecture and contract

### FR-005: Layer rule
- `scripts/check_layers.py` passes: `domain` imports only the standard library,
  `application` adds only the OpenTelemetry API, inbound and outbound adapters don't import
  each other, and only `bootstrap.py` wires adapters (ADR 0002).

### FR-006: Provided contract
- `contracts/openapi.yaml` is valid OpenAPI 3.1 with a semver `info.version`, passes
  `scripts/contract_check.py` against `main`, and the HTTP adapter's responses for
  `/healthz` and errors validate against its schemas.

### FR-007: Container image
- Two-stage build from `uv.lock` with a uv-managed interpreter, non-root runtime, pinned
  base images, `HEALTHCHECK` on `/healthz`, listening on `$PORT` (default `8080`).

## Gateway contract

### FR-008: Contract v0.1.0 (agent-platform ADR 0004)
- `contracts/openapi.yaml` declares `POST /anthropic/v1/messages` (gateway key as
  `x-api-key`, Anthropic error shape), `POST /openai/v1/chat/completions` (gateway key as
  bearer, OpenAI error shape) and `GET /v1/models`.
- Both dialect endpoints accept `x-agent-thread`, `x-agent-route` (`interactive` /
  `background`), `x-agent-identity`, `x-agent-deadline` and `x-gateway-placement`, require
  only the fields the gateway reads (`model`, `messages`, plus `max_tokens` for Anthropic),
  allow every other field through, return JSON or `text/event-stream`, report
  `x-gateway-provider`, and document `429` with `retry-after`.
- The Anthropic response schema keeps `usage.cache_read_input_tokens` and
  `usage.cache_creation_input_tokens`.
- Each served model lists its real `id`, a `dialect` (`anthropic` / `openai`) and
  capabilities: `tool_calling`, `max_input_tokens`, `prompt_caching`
  (`explicit` / `automatic` / `none`), `thinking`.

## Non-functional requirements

### NFR-001: Reproducible environment
- uv-managed interpreter, committed `uv.lock`, image built with `--locked`.

### NFR-002: Telemetry never breaks the service
- **Given** the OTLP collector is unreachable, **when** requests arrive, **then** they
  succeed and shutdown completes in bounded time.

### NFR-003: Every requirement is tested
- Each ID here is cited by a test, and no test cites an unknown ID.

### NFR-004: Offline default tests
- The default suite opens no non-loopback network connection. Tests needing real external
  services are marked `live` and opt-in.
