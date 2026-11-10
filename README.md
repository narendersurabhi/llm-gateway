# llm-gateway

LLM gateway for the agent services: native Anthropic and OpenAI-compatible endpoints with placement, capacity limits and usage accounting.

Part of the agent services; cross-service conventions and decisions live in
[agent-platform](https://github.com/narendersurabhi/agent-platform). Created from its
service template (`.copier-answers.yml` records the version).

## Quick start

```bash
uv sync                      # uv-managed Python + locked dependencies in .venv
uv run pre-commit install    # git hooks: the only quality gate (no hosted CI)
uv run pytest                # offline suite
uv run llm-gateway    # http://localhost:8080/healthz
docker compose up --build    # with Grafana LGTM on http://localhost:3000
```

## Layout (ports and adapters)

| Path | Holds | May import |
|---|---|---|
| `src/llm_gateway/domain/` | entities, value objects, errors | standard library |
| `src/llm_gateway/application/` | use cases, ports | + domain, OpenTelemetry API |
| `src/llm_gateway/adapters/inbound/` | HTTP routes, queue consumers | anything but outbound adapters and bootstrap |
| `src/llm_gateway/adapters/outbound/` | port implementations | anything but inbound adapters and bootstrap |
| `src/llm_gateway/infrastructure/` | settings, telemetry setup | anything but adapters and bootstrap |
| `src/llm_gateway/bootstrap.py` | the composition root | anything |

## Contract

The provided contract is [contracts/openapi.yaml](contracts/openapi.yaml) (MCP tools, if
any, in `contracts/mcp/`). Change it only with a version bump; breaking changes bump MAJOR
and keep the old major served. Contracts this service consumes are pinned under
`contracts/consumed/<provider>/`.

## Configuration

| Variable | Default | |
|---|---|---|
| `LLM_GATEWAY_ENVIRONMENT` | `dev` | `deployment.environment.name` |
| `LLM_GATEWAY_LOG_LEVEL` | `INFO` | level exported through OTLP |
| `PORT` | `8080` | listen port |
| `OTEL_TRACES_EXPORTER` / `OTEL_LOGS_EXPORTER` / `OTEL_METRICS_EXPORTER` | `none` | `none`, `console` or `otlp` |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | | OTLP/HTTP collector |
| `OTEL_SERVICE_NAME` | `llm-gateway` | `service.name` |

## Process

Spec-driven: see [specs/constitution.md](specs/constitution.md) and
[specs/README.md](specs/README.md). Pull template updates with `uvx copier update --trust`.
