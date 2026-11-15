# Tasks 002: The gateway on Envoy AI Gateway

## Tests first
- [x] T001 Acceptance suite in `tests/acceptance/` from the spike's 14 checks, tagged with requirement IDs (FR-009–FR-015, FR-017, FR-018, NFR-005, NFR-006)
- [x] T002 New acceptance checks: discovery and unknown model (S14), placement constraint (S15), self-hosted capacity (S16), GenAI metrics (S17) (FR-011, FR-012, FR-014, FR-017)
- [x] T003 Offline configuration checks, `xfail(strict=True)` until `deploy/` exists (FR-009, FR-012–FR-015, FR-017, FR-019–FR-021, NFR-005, NFR-007)

## Implementation
- [ ] T004 Evaluate manifest and CEL validators (kubeconform, server-side dry run, cel-go, cel-python); record the choice in plan.md (FR-019)
- [ ] T005 `deploy/versions.env` and Helm installs at pinned versions (FR-020)
- [ ] T006 `deploy/gateway/base` from the spike config: routes, backends, policies, access log, 429 override (FR-009–FR-015, FR-017, FR-018)
- [ ] T007 kind overlay and `deploy/kind/acceptance.sh`; acceptance green, report committed (FR-020, NFR-007)
- [ ] T008 Placement-constraint rule and self-hosted circuit breaker (FR-012, FR-014)
- [ ] T009 Validation hook in `.pre-commit-config.yaml` with the chosen tools (FR-019)
- [ ] T010 EKS overlay: Secrets, NLB service, replicas (FR-015, FR-020)
- [ ] T011 Contract v1.0.0 and spec 001 FR-008 tests updated (FR-021)
- [ ] T012 Answer FR-013's open question (do Envoy's `input_tokens` include Anthropic cache writes?) with a cache-write fixture

## Delivery
- [ ] T013 Remove the `xfail` markers; README (install, acceptance, upgrade procedure)
- [ ] T014 `branch_policy.py --ready` passes, push, PR into `main`
