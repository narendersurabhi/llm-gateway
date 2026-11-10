# CLAUDE.md

`llm-gateway`: LLM gateway for the agent services: native Anthropic and OpenAI-compatible endpoints with placement, capacity limits and usage accounting. Spec-driven: read `specs/constitution.md` and
`specs/README.md` before changing behaviour. Cross-service rules are in agent-platform's
`docs/conventions.md` and its ADRs; this repo was stamped from its template.

## Rules

- **One feature, one branch:** `spec/NNN-short-name` matching `specs/NNN-short-name/`, or
  `fix/`, `chore/`, `docs/`. Never commit to `main`. Run
  `uv run python scripts/branch_policy.py --ready` before a PR.
- **Hooks are the gate** (`uv run pre-commit install`); never `--no-verify`.
- **Spec before code:** new `FR-###`/`NFR-###` (never reuse or renumber), tests tagged
  `@pytest.mark.spec(...)` written first. Keep `plan.md`/`tasks.md` in sync.
- **Ports and adapters (ADR 0002):** use cases in `application/` depend on `Protocol` ports;
  frameworks only in `adapters/`, `infrastructure/` and `bootstrap.py`. Policy (validation,
  authorization) lives in use cases, not routes. `scripts/check_layers.py` enforces it.
- **Contracts (ADR 0003):** bump `info.version` in `contracts/openapi.yaml` on every change;
  breaking changes bump MAJOR. Consumed contracts are pinned in `contracts/consumed/`.
- **Telemetry:** spans via `application/telemetry.py` (`tracer`, `meter`, `mark_error`);
  metric attributes bounded; no user content or exception messages in any signal. Tests
  assert with the `spans` / `logs` / `collect_metrics` fixtures.
- **Offline tests:** the conftest blocks non-loopback sockets; real services are `live` tests.

## Commands

```bash
uv sync && uv run pre-commit install
uv run pytest
uv run python scripts/check_layers.py
uv run python scripts/contract_check.py
uv run python scripts/spec_coverage.py
uv run pre-commit run --all-files --hook-stage pre-push
```
