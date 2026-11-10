# Specs

This project uses **spec-driven development** (SDD). Specs are not after-the-fact documentation.
They are the contract that code and tests are written against.

```
specs/
├── constitution.md              # non-negotiable project principles
└── NNN-feature-name/
    ├── spec.md                  # WHAT and WHY: requirements + acceptance criteria (IDs)
    ├── plan.md                  # HOW: architecture, modules, data contracts
    └── tasks.md                 # ordered, checkable work items, each tied to requirement IDs
```

| Spec | Status |
|---|---|
| [001-service-skeleton](001-service-skeleton/spec.md): hexagonal skeleton, telemetry, contract, container | Implemented |

## Workflow

0. **Branch.** `git switch -c spec/NNN-short-name main`. One feature per branch, with the
   branch name matching the spec folder (constitution IX). Hooks must be installed once per
   clone: `uv run pre-commit install`.
1. **Specify.** Copy `_template/` to `specs/NNN-short-name/` and write `spec.md`. Give each
   requirement a new `FR-###` / `NFR-###` ID and Given/When/Then acceptance criteria.
   Use **[NEEDS CLARIFICATION: …]** for anything unresolved. A spec isn't ready while any remain.
2. **Plan.** Write `plan.md`: which modules change, data contracts, trade-offs. Check it
   against `constitution.md`.
3. **Task.** Break the plan into `tasks.md`. Each task lists the requirement IDs it satisfies.
4. **Test first.** Write failing tests tagged `@pytest.mark.spec("FR-###")`.
5. **Implement** until the tests pass, then tick the tasks off.
6. **Verify.** `uv run pytest` runs the traceability check, which fails if any requirement
   has no test or a test cites an unknown ID.
7. **Pull request.** Run `uv run python scripts/branch_policy.py --ready`, which checks that
   all tasks are ticked. Then push (the pre-push hook runs the full test suite) and open a PR
   into `main`. The template has a checklist. `main` rejects direct pushes.

Suggested commit sequence on a feature branch, so the SDD order shows in history:
`spec: …` (spec/plan/tasks) → `test: …` (failing tests) → `feat: …` (implementation and docs).

| Branch | For | Policy |
|---|---|---|
| `spec/NNN-short-name` | a feature from `specs/NNN-short-name/` | spec, plan and tasks must exist; all tasks ticked |
| `fix/<name>` | a bug fix | cite the violated requirement ID in the PR |
| `chore/<name>` | hooks, tooling, dependencies, refactors | no behaviour change |
| `docs/<name>` | documentation only | may not touch `src/`, `tests/`, `scripts/`, `deploy/` or container files |

## Local quality gates (instead of hosted CI)

| Hook | When | Checks |
|---|---|---|
| `branch-policy` | commit, push | not on `main`; branch naming; `specs/NNN-*/` folder for `spec/` branches; `docs/` doesn't touch code |
| `ruff-check`, `ruff-format` | commit | lint and formatting of staged Python files |
| `uv-lock` | commit (when `pyproject.toml`/`uv.lock` change) | lockfile in sync |
| `spec-traceability` | commit (when `specs/` or `tests/` change) | every requirement has a test, no unknown IDs |
| `check-layers` | commit (when `src/` changes) | ports-and-adapters dependency rule (ADR 0002) |
| `contract-check` | commit (when `contracts/` changes) | contracts valid, versioned, breaking changes bump MAJOR (ADR 0003) |
| `pytest` | push | full offline suite |

Run everything on demand with `uv run pre-commit run --all-files --hook-stage pre-push`.
