## What

<!-- One or two sentences. Feature PRs: link the spec, e.g. specs/002-streaming/spec.md -->

**Branch type:** `spec/NNN-…` feature · `fix/…` · `chore/…` · `docs/…`

## Requirements

<!-- Requirement IDs this PR adds, changes or fixes, e.g. FR-020, FR-021 (amended), NFR-006 -->

## Checklist

- [ ] Spec updated **before** code (new IDs never reuse old ones)
- [ ] Every new/changed requirement has a test tagged `@pytest.mark.spec(...)`
- [ ] New stages emit spans, correlated logs and bounded-cardinality metrics, asserted in tests
- [ ] `plan.md` / `tasks.md` reflect what was built; all tasks ticked
- [ ] Git hooks installed and green on every commit/push (no `--no-verify`)
- [ ] `uv run python scripts/branch_policy.py --ready` passes
- [ ] README / config example updated if user-facing behaviour changed
