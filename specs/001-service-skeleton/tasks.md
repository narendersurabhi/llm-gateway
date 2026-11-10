# Tasks 001: Service skeleton

## Tests first
- [x] T001 Fixtures: in-memory exporters, loopback-only network guard, app factory (NFR-004)
- [x] T002 Health, error envelope and contract-conformance tests (FR-001, FR-002, FR-006)
- [x] T003 Telemetry tests, including an unreachable collector in a fresh process (FR-003, FR-004, NFR-002)
- [x] T004 Layer, container and traceability tests (FR-005, FR-007, NFR-001, NFR-003)

## Implementation
- [x] T005 Domain errors, `Clock` port, `CheckHealth` use case (FR-001, FR-002)
- [x] T006 HTTP adapter, settings, telemetry setup, composition root (FR-001–FR-004)
- [x] T007 Contract, Dockerfile, compose (FR-006, FR-007)

## Gateway contract
- [x] T009 Contract-content tests for v0.1.0 (FR-008)
- [x] T010 `contracts/openapi.yaml` v0.1.0 per ADR 0004 (FR-008)

## Delivery
- [x] T008 README and CLAUDE.md
- [x] T011 `branch_policy.py --ready` passes, push (hooks green), PR into `main`
