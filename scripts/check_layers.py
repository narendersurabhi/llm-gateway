"""Ports-and-adapters layer check (ADR 0002). Runs as a git hook and in the tests.

For the package under src/<package>/:

    domain          standard library and <package>.domain only
    application     + <package>.application and the OpenTelemetry *API*
                      (opentelemetry.sdk / .exporter / .instrumentation are infrastructure)
    adapters        anything except bootstrap; inbound and outbound adapters never import
                      each other (inbound goes through a use case)
    infrastructure  anything except adapters and bootstrap
    bootstrap       anything: it is the composition root
    __main__        anything

Extra modules a service lets its application layer import (rare: a pure library such as
`pydantic` for value objects) go in pyproject.toml:

    [tool.check-layers]
    application-allow = ["pydantic"]

Usage: python scripts/check_layers.py [--root DIR]
"""

from __future__ import annotations

import argparse
import ast
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STDLIB = set(sys.stdlib_module_names) | {"__future__"}
OTEL_NOT_API = ("opentelemetry.sdk", "opentelemetry.exporter", "opentelemetry.instrumentation")


@dataclass(frozen=True)
class Violation:
    path: Path
    line: int
    module: str
    imported: str
    rule: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: {self.module} imports {self.imported} ({self.rule})"


def find_package(root: Path) -> Path:
    candidates = [p for p in (root / "src").iterdir() if (p / "__init__.py").is_file()]
    if len(candidates) != 1:
        raise SystemExit(f"check-layers: expected exactly one package in {root / 'src'}")
    return candidates[0]


def module_name(pkg_dir: Path, path: Path) -> str:
    rel = path.relative_to(pkg_dir.parent).with_suffix("")
    parts = list(rel.parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def imports(tree: ast.AST, module: str, is_package: bool) -> list[tuple[int, str]]:
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found += [(node.lineno, alias.name) for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = module.split(".")
                base = base if is_package else base[:-1]
                base = base[: len(base) - (node.level - 1)]
                target = ".".join([*base, node.module] if node.module else base)
            else:
                target = node.module or ""
            if node.module is None and node.level:
                found += [(node.lineno, f"{target}.{a.name}") for a in node.names]
            else:
                found.append((node.lineno, target))
    return found


def layer_of(module: str, pkg: str) -> str:
    rest = module[len(pkg) + 1 :] if module.startswith(pkg + ".") else ""
    head = rest.split(".")[0] if rest else ""
    if head == "adapters":
        parts = rest.split(".")
        return f"adapters.{parts[1]}" if len(parts) > 1 else "adapters"
    return head or "root"


def check_import(module: str, imported: str, pkg: str, app_allow: set[str]) -> str | None:
    """The rule an import breaks, or None."""
    layer = layer_of(module, pkg)
    top = imported.split(".")[0]
    internal = imported == pkg or imported.startswith(pkg + ".")
    target = layer_of(imported, pkg) if internal else None

    if layer == "domain":
        if internal and target != "domain":
            return "domain may only import domain"
        if not internal and top not in STDLIB:
            return "domain may only import the standard library"
    elif layer == "application":
        if internal and target not in ("domain", "application"):
            return "application may only import domain and application"
        if not internal and top not in STDLIB:
            if top == "opentelemetry" and not imported.startswith(OTEL_NOT_API):
                return None
            if top in app_allow:
                return None
            return "application may only import the standard library and the OpenTelemetry API"
    elif layer.startswith("adapters"):
        if target == "bootstrap":
            return "adapters may not import the composition root"
        sides = ("adapters.inbound", "adapters.outbound")
        if layer in sides and target in sides and target != layer:
            return "inbound and outbound adapters may not import each other"
    elif (
        layer == "infrastructure"
        and target is not None
        and (target.startswith("adapters") or target == "bootstrap")
    ):
        return "infrastructure may not import adapters or the composition root"
    return None


def check(root: Path) -> list[Violation]:
    pkg_dir = find_package(root)
    pkg = pkg_dir.name
    app_allow: set[str] = set()
    pyproject = root / "pyproject.toml"
    if pyproject.is_file():
        cfg = tomllib.loads(pyproject.read_text()).get("tool", {}).get("check-layers", {})
        app_allow = set(cfg.get("application-allow", []))
    violations = []
    for path in sorted(pkg_dir.rglob("*.py")):
        module = module_name(pkg_dir, path)
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for line, imported in imports(tree, module, path.name == "__init__.py"):
            rule = check_import(module, imported, pkg, app_allow)
            if rule:
                violations.append(Violation(path.relative_to(root), line, module, imported, rule))
    return violations


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args(argv)
    violations = check(args.root.resolve())
    for v in violations:
        print(f"check-layers: {v}", file=sys.stderr)
    return 1 if violations else 0


if __name__ == "__main__":
    sys.exit(main())
