"""Branch policy (constitution IX: one feature, one branch). Runs as a git hook.

    spec/NNN-short-name   a feature. specs/NNN-short-name/ must exist with spec.md,
                          plan.md and tasks.md. With --ready, every task must be ticked.
    fix/<name>            a bug fix (cite the violated requirement ID in the PR).
    chore/<name>          tooling, hooks, dependencies, refactors without behaviour change.
    docs/<name>           documentation only. May not touch code, tests or deployment files.

Usage:
    python scripts/branch_policy.py              # current branch; changes vs origin/main
    python scripts/branch_policy.py --ready      # also require all tasks ticked (before a PR)
    python scripts/branch_policy.py --branch B [--changed FILE ...]
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROTECTED = {"main", "master"}
BRANCH = re.compile(r"^(?P<kind>spec|fix|chore|docs)/(?P<rest>[a-z0-9][a-z0-9._-]*)$")
SPEC_BRANCH = re.compile(r"^(?P<num>\d{3})-[a-z0-9][a-z0-9-]*$")
SPEC_FILES = ("spec.md", "plan.md", "tasks.md")
CODE = ("src/", "tests/", "scripts/", "deploy/", "Dockerfile", "docker-compose")


def violations(
    branch: str, changed: list[str], root: Path = ROOT, ready: bool = False
) -> list[str]:
    if branch == "HEAD":
        return []  # detached HEAD (rebase, bisect): nothing to check
    if branch in PROTECTED:
        return [f"don't commit to '{branch}': git switch -c spec/NNN-name (or fix/, chore/, docs/)"]
    m = BRANCH.match(branch)
    if not m:
        return [f"branch '{branch}' must be spec/NNN-name, fix/<name>, chore/<name> or docs/<name>"]
    kind, rest = m["kind"], m["rest"]
    problems = []

    if kind == "spec":
        if not SPEC_BRANCH.match(rest):
            return [f"feature branch '{branch}' must look like spec/NNN-short-name"]
        spec_dir = root / "specs" / rest
        if not spec_dir.is_dir():
            return [f"spec branch '{branch}' needs specs/{rest}/ (copy specs/_template/)"]
        problems += [
            f"specs/{rest}/{name} is missing"
            for name in SPEC_FILES
            if not (spec_dir / name).is_file()
        ]
        tasks = spec_dir / "tasks.md"
        if ready and tasks.is_file():
            open_tasks = [
                line.strip()
                for line in tasks.read_text(encoding="utf-8").splitlines()
                if line.lstrip().startswith("- [ ]")
            ]
            problems += [f"unfinished task in specs/{rest}/tasks.md: {t}" for t in open_tasks]

    if kind == "docs":
        problems += [f"docs branch changes code: {f}" for f in changed if f.startswith(CODE)]

    return problems


def _git(*args: str) -> str:
    result = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)
    return result.stdout.strip() if result.returncode == 0 else ""


def current_branch() -> str:
    # symbolic-ref also works on a branch with no commits yet; detached HEAD gives "".
    return _git("symbolic-ref", "--short", "-q", "HEAD") or "HEAD"


def _changed_vs_main() -> list[str]:
    for base in ("origin/main", "main"):
        if _git("rev-parse", "--verify", "--quiet", base):
            committed = _git("diff", "--name-only", f"{base}...HEAD").splitlines()
            staged = _git("diff", "--name-only", "--cached").splitlines()
            return sorted(set(committed) | set(staged))
    return []


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--branch", help="defaults to the current branch")
    parser.add_argument("--ready", action="store_true", help="require all tasks ticked")
    parser.add_argument("--changed", nargs="*", help="defaults to the diff against main")
    args = parser.parse_args(argv)

    branch = args.branch or current_branch()
    changed = args.changed if args.changed is not None else _changed_vs_main()
    problems = violations(branch, changed, ready=args.ready)
    for p in problems:
        print(f"branch-policy: {p}", file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
