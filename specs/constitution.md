# Project Constitution

These principles govern every change to `llm-gateway`. Specs, plans and reviews check
against them. Cross-service rules come from `docs/conventions.md` in `agent-platform`
(the version this repo follows is in `.copier-answers.yml`).

## I. Spec first
Behaviour is defined in `specs/NNN-feature/spec.md` **before** it is implemented. A pull
request that changes behaviour updates the spec in the same PR. If code and spec disagree,
the spec is the source of truth and the code is the bug.

## II. Every requirement is testable and tested
Each requirement has a stable ID (`FR-###`, `NFR-###`) and Given/When/Then acceptance
criteria, and is cited by at least one test via `@pytest.mark.spec("FR-###")`.
`tests/test_spec_traceability.py` enforces this and rejects citations of unknown IDs.

## III. Requirement IDs are permanent
IDs are never renumbered or reused. A dropped requirement is marked
`~~FR-###~~ (withdrawn: reason)` and stays in the spec.

## IV. Safe by default
Authentication is on, authorization comes from `x-agent-identity` (never from request
bodies or tool arguments), secrets never appear in images, templates or logs, and user
content never goes into telemetry. Loosening a default needs an explicit setting and a
spec change.

## V. Reproducible environment
uv-managed interpreter (`python-preference = "only-managed"`), committed `uv.lock`, images
built with `--locked`, base images pinned to exact versions.

## VI. Observable from day one
Every unit of work emits OpenTelemetry spans with domain attributes, JSON logs correlated
by `trace_id`/`span_id`, and bounded-cardinality metrics linked to traces by exemplars.
Tests assert on the telemetry. Telemetry failures never break the service.

## VII. Ports and adapters
`domain` is standard library only; `application` adds only the OpenTelemetry API;
frameworks live in `adapters`, `infrastructure` and the `bootstrap.py` composition root
(ADR 0002, enforced by `scripts/check_layers.py`).

## VIII. Contracts are versioned
The provided contract in `contracts/` changes only with a version bump, and a breaking
change bumps MAJOR while the old major stays served (ADR 0003, enforced by
`scripts/contract_check.py`). Consumed contracts are pinned copies in `contracts/consumed/`.

## IX. One feature, one branch
Each spec is built on `spec/NNN-short-name` and reaches `main` only through a pull request;
`main` is protected. Bug fixes use `fix/…`, tooling `chore/…`, docs `docs/…`. Quality gates
are local git hooks (no hosted CI): every commit runs branch policy, lint/format, lockfile,
layers, contracts and traceability; every push runs the full offline suite. A PR is opened
only after `scripts/branch_policy.py --ready` passes. Never use `--no-verify`.
