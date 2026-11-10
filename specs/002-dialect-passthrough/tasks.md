# Tasks 002: Dialect pass-through, placement and capacity

## Tests first
- [x] T001 Behaviour tests for FR-009–FR-017, NFR-005, NFR-006, marked `xfail(strict=True)` until implemented

## Implementation
- [ ] T002 Domain: served models, placements, `choose_placement`, errors (FR-011, FR-012)
- [ ] T003 `GatewayConfig` and client keys (FR-011, FR-015)
- [ ] T004 `ForwardRequest` use case and ports (FR-009–FR-016)
- [ ] T005 Outbound providers on httpx with injectable transport (FR-009, FR-010)
- [ ] T006 HTTP routes: raw-body pass-through, streaming relay, native error shapes (FR-009–FR-011)
- [ ] T007 Limiters: in-memory and Redis (FR-013, FR-014)
- [ ] T008 Spans, metrics and usage ledger (FR-017, NFR-005)
- [ ] T009 Overhead benchmark test (NFR-006)

## Delivery
- [ ] T010 Remove the `xfail` markers; README configuration section; example `gateway.yaml`
- [ ] T011 `branch_policy.py --ready` passes, push, PR into `main`
