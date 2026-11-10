"""Requirement -> test traceability for spec-driven development.

Requirement IDs come from headings in specs/*/spec.md (`### FR-001: Title`). Test references
come from `@pytest.mark.spec("FR-001", ...)` usages under tests/, found by AST.

Usage: uv run python scripts/spec_coverage.py   (exit code 1 if any gap)
"""

from __future__ import annotations

import ast
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPECS_DIR = ROOT / "specs"
TESTS_DIR = ROOT / "tests"

# Matches "### FR-001: Title" but not withdrawn "### ~~FR-001~~".
REQ_HEADING = re.compile(r"^#{2,4}\s+((?:FR|NFR)-\d{3})\b", re.MULTILINE)


def spec_requirements(specs_dir: Path = SPECS_DIR) -> dict[str, Path]:
    reqs: dict[str, Path] = {}
    for spec in sorted(specs_dir.glob("*/spec.md")):
        if spec.parent.name.startswith("_"):
            continue  # templates
        for req_id in REQ_HEADING.findall(spec.read_text(encoding="utf-8")):
            if req_id in reqs:
                raise ValueError(f"Duplicate requirement ID {req_id} in {spec} and {reqs[req_id]}")
            reqs[req_id] = spec
    return reqs


def _is_spec_marker(node: ast.AST) -> bool:
    # pytest.mark.spec(...)
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "spec"
        and isinstance(node.func.value, ast.Attribute)
        and node.func.value.attr == "mark"
    )


def find_test_references(tests_dir: Path = TESTS_DIR) -> dict[str, list[str]]:
    """Map requirement ID -> list of 'file::test' that cite it."""
    refs: dict[str, list[str]] = defaultdict(list)
    for path in sorted(tests_dir.rglob("test_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        rel = path.relative_to(tests_dir.parent)
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.ClassDef, ast.AsyncFunctionDef)):
                for dec in node.decorator_list:
                    if _is_spec_marker(dec):
                        for arg in dec.args:
                            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                                refs[arg.value].append(f"{rel}::{node.name}")
    return dict(refs)


def check() -> tuple[list[str], list[str], dict[str, list[str]], dict[str, Path]]:
    reqs = spec_requirements()
    refs = find_test_references()
    uncovered = sorted(r for r in reqs if r not in refs)
    unknown = sorted(r for r in refs if r not in reqs)
    return uncovered, unknown, refs, reqs


def main() -> int:
    uncovered, unknown, refs, reqs = check()
    width = max((len(r) for r in reqs), default=6)
    for req_id in sorted(reqs, key=lambda r: (r.split("-")[0], r)):
        tests = refs.get(req_id, [])
        mark = "✔" if tests else "✘"
        print(f"{mark} {req_id:<{width}}  {len(tests):>2} test(s)")
        for t in tests:
            print(f"      {t}")
    if uncovered:
        print(f"\nUncovered requirements: {', '.join(uncovered)}")
    if unknown:
        print(f"Tests cite unknown requirement IDs: {', '.join(unknown)}")
    covered = len(reqs) - len(uncovered)
    print(f"\nSpec coverage: {covered}/{len(reqs)} requirements")
    return 1 if uncovered or unknown else 0


if __name__ == "__main__":
    sys.exit(main())
