# Plan NNN: <Feature name>

Implements [spec.md](spec.md). Checked against [constitution](../constitution.md).

## Architecture
<!-- Diagram or description of the flow and where the change fits. -->

## Modules
| Module | Responsibility | Requirements |
|---|---|---|
| | | |

## Key decisions
<!-- Each decision with the alternative you rejected and why. -->

## Constitution check
- [ ] I. Spec updated before code
- [ ] II. Every new ID has a test
- [ ] III. No IDs renumbered or reused
- [ ] IV. Safe defaults kept (auth on, no host fs/shell, no content in telemetry)
- [ ] V. Dependencies pinned in uv.lock
- [ ] VI. New stages emit spans, correlated logs and bounded-cardinality metrics, asserted in tests
- [ ] VII. Default tests stay offline
- [ ] VIII. No hook or test creates cloud resources

## Risks
