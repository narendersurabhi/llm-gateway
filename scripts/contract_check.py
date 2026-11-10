"""Contract checks: validity, semver, and backwards compatibility (docs/conventions.md, Contracts).

A service's contracts live in `contracts/`:
    contracts/openapi.yaml        OpenAPI 3.1, `info.version` is the contract version
    contracts/mcp/*.json          MCP tool definitions with a top-level `version`
    contracts/consumed/**         pinned copies of providers' contracts (validated, never diffed)

Each provided contract is compared with its copy on the base ref (origin/main, else main):
    breaking change  -> the MAJOR version must increase
    any other change -> the version must increase
    no change        -> nothing to do

Breaking means an existing consumer's correct request becomes invalid, or something in a
response it may rely on disappears or changes type. Consumers are tolerant readers (they
ignore unknown fields and enum values), so additions to responses are not breaking.

Usage:
    python scripts/contract_check.py [--base REF] [--root DIR]
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
METHODS = ("get", "put", "post", "delete", "patch", "head", "options", "trace")
REQUEST, RESPONSE = "request", "response"

Resolve = Callable[[Any], Any]


def load(text: str) -> Any:
    return yaml.safe_load(text)


def semver(version: object) -> tuple[int, int, int] | None:
    m = SEMVER.match(str(version)) if version is not None else None
    return (int(m[1]), int(m[2]), int(m[3])) if m else None


def resolver(doc: dict) -> Resolve:
    """Follow local `$ref`s (`#/components/schemas/X`), guarding against cycles."""

    def resolve(node: Any, _seen: frozenset[str] = frozenset()) -> Any:
        if not isinstance(node, dict) or "$ref" not in node:
            return node
        ref = node["$ref"]
        if not isinstance(ref, str) or not ref.startswith("#/") or ref in _seen:
            return {}
        target: Any = doc
        for part in ref[2:].split("/"):
            target = (
                target.get(part.replace("~1", "/").replace("~0", "~"), {})
                if isinstance(target, dict)
                else {}
            )
        return resolve(target, _seen | {ref})

    return resolve


def _types(schema: dict) -> set[str] | None:
    t = schema.get("type")
    if t is None:
        return None
    return set(t) if isinstance(t, list) else {t}


def schema_breaks(old: Any, new: Any, where: str, direction: str, resolve: Resolve) -> list[str]:
    """Breaking differences between two JSON Schemas used as a request or a response."""
    old, new = resolve(old), resolve(new)
    if not isinstance(old, dict) or not isinstance(new, dict):
        return []
    out: list[str] = []
    request = direction == REQUEST

    old_t, new_t = _types(old), _types(new)
    if old_t is not None and new_t is not None:
        # Requests: the server must still accept every type it accepted.
        # Responses: the server may only send types consumers already handle.
        lost = old_t - new_t if request else new_t - old_t
        if lost:
            out.append(f"{where}: type changed {sorted(old_t)} -> {sorted(new_t)}")

    if request and "enum" in old:
        removed = [v for v in old["enum"] if v not in new.get("enum", old["enum"])]
        if removed:
            out.append(f"{where}: enum values no longer accepted: {removed}")

    old_props, new_props = old.get("properties", {}), new.get("properties", {})
    old_req, new_req = set(old.get("required", [])), set(new.get("required", []))
    if request:
        for name in sorted(new_req - old_req):
            out.append(f"{where}: '{name}' is newly required")
        if new.get("additionalProperties") is False:
            for name in sorted(set(old_props) - set(new_props)):
                out.append(f"{where}: field '{name}' removed and extra fields are rejected")
        for key, tighter in (("maxLength", min), ("maxItems", min), ("maximum", min),
                             ("minLength", max), ("minItems", max), ("minimum", max)):  # fmt: skip
            if key in new and (key not in old or tighter(old[key], new[key]) != old[key]):
                out.append(f"{where}: {key} narrowed {old.get(key, 'unset')} -> {new[key]}")
    else:
        for name in sorted(set(old_props) - set(new_props)):
            out.append(f"{where}: field '{name}' removed")
        for name in sorted(old_req - new_req):
            if name in new_props:
                out.append(f"{where}: field '{name}' is no longer guaranteed")

    for name in sorted(set(old_props) & set(new_props)):
        out += schema_breaks(
            old_props[name], new_props[name], f"{where}.{name}", direction, resolve
        )
    if "items" in old and "items" in new:
        out += schema_breaks(old["items"], new["items"], f"{where}[]", direction, resolve)
    return out


def _params(path_item: dict, op: dict, resolve: Resolve) -> dict[tuple[str, str], dict]:
    params = {}
    for raw in [*path_item.get("parameters", []), *op.get("parameters", [])]:
        p = resolve(raw)
        if isinstance(p, dict) and "name" in p and "in" in p:
            params[(p["name"], p["in"])] = p
    return params


def openapi_breaks(old: dict, new: dict) -> list[str]:
    ro, rn = resolver(old), resolver(new)
    out: list[str] = []
    new_paths = new.get("paths", {}) or {}
    for path, old_item in (old.get("paths", {}) or {}).items():
        if path not in new_paths:
            out.append(f"path {path} removed")
            continue
        new_item = new_paths[path]
        for method in METHODS:
            if method not in old_item:
                continue
            where = f"{method.upper()} {path}"
            if method not in new_item:
                out.append(f"{where} removed")
                continue
            old_op, new_op = old_item[method], new_item[method]

            old_params, new_params = _params(old_item, old_op, ro), _params(new_item, new_op, rn)
            for key, p in new_params.items():
                if p.get("required") and not old_params.get(key, {}).get("required"):
                    out.append(f"{where}: {key[1]} parameter '{key[0]}' is newly required")
                elif key in old_params:
                    out += _cross(old_params[key].get("schema"), p.get("schema"),
                                  f"{where} param {key[0]}", REQUEST, ro, rn)  # fmt: skip

            old_body, new_body = (
                ro(old_op.get("requestBody", {})),
                rn(new_op.get("requestBody", {})),
            )
            if new_body.get("required") and not old_body.get("required"):
                out.append(f"{where}: request body is newly required")
            for media, content in (old_body.get("content", {}) or {}).items():
                new_content = (new_body.get("content", {}) or {}).get(media)
                if new_content is None:
                    out.append(f"{where}: request media type {media} no longer accepted")
                else:
                    out += _cross(content.get("schema"), new_content.get("schema"),
                                  f"{where} request", REQUEST, ro, rn)  # fmt: skip

            new_responses = new_op.get("responses", {}) or {}
            for status, old_resp in (old_op.get("responses", {}) or {}).items():
                if not str(status).startswith("2"):
                    continue
                new_resp = new_responses.get(status)
                if new_resp is None:
                    out.append(f"{where}: response {status} removed")
                    continue
                old_resp, new_resp = ro(old_resp), rn(new_resp)
                for header in old_resp.get("headers", {}) or {}:
                    if header not in (new_resp.get("headers", {}) or {}):
                        out.append(f"{where} {status}: response header '{header}' removed")
                for media, content in (old_resp.get("content", {}) or {}).items():
                    new_content = (new_resp.get("content", {}) or {}).get(media)
                    if new_content is None:
                        out.append(f"{where} {status}: media type {media} removed")
                    else:
                        out += _cross(content.get("schema"), new_content.get("schema"),
                                      f"{where} {status}", RESPONSE, ro, rn)  # fmt: skip
    return out


def _cross(old: Any, new: Any, where: str, direction: str, ro: Resolve, rn: Resolve) -> list[str]:
    """Compare schemas from two documents: inline each side's $refs, then diff."""
    old, new = _deep_resolve(old, ro), _deep_resolve(new, rn)
    return schema_breaks(old, new, where, direction, lambda node: node)


def _deep_resolve(node: Any, resolve: Resolve, depth: int = 0) -> Any:
    if depth > 32:
        return {}
    node = resolve(node)
    if isinstance(node, dict):
        return {k: _deep_resolve(v, resolve, depth + 1) for k, v in node.items()}
    if isinstance(node, list):
        return [_deep_resolve(v, resolve, depth + 1) for v in node]
    return node


def mcp_breaks(old: dict, new: dict) -> list[str]:
    out = []
    if old.get("name") != new.get("name"):
        out.append(f"tool renamed {old.get('name')!r} -> {new.get('name')!r}")
    ident = lambda node: node  # noqa: E731
    out += schema_breaks(_deep_resolve(old.get("inputSchema", {}), ident),
                         _deep_resolve(new.get("inputSchema", {}), ident),
                         "inputSchema", REQUEST, ident)  # fmt: skip
    if "outputSchema" in old:
        if "outputSchema" not in new:
            out.append("outputSchema removed")
        else:
            out += schema_breaks(old["outputSchema"], new["outputSchema"], "outputSchema",
                                 RESPONSE, ident)  # fmt: skip
    return out


@dataclass(frozen=True)
class Kind:
    name: str
    version: Callable[[dict], object]
    validate: Callable[[dict], list[str]]
    breaks: Callable[[dict, dict], list[str]]


def _validate_openapi(doc: dict) -> list[str]:
    from openapi_spec_validator import validate

    if not str(doc.get("openapi", "")).startswith("3.1"):
        return [f"openapi must be 3.1.x, got {doc.get('openapi')!r}"]
    try:
        validate(doc)
    except Exception as exc:  # the validator raises several exception types
        return [f"invalid OpenAPI document: {str(exc).splitlines()[0]}"]
    return []


def _validate_mcp(doc: dict) -> list[str]:
    from jsonschema import Draft202012Validator
    from jsonschema.exceptions import SchemaError

    required = ("name", "description", "inputSchema", "version")
    problems = [f"MCP tool definition lacks '{k}'" for k in required if k not in doc]
    for key in ("inputSchema", "outputSchema"):
        if key in doc:
            try:
                Draft202012Validator.check_schema(doc[key])
            except SchemaError as exc:
                problems.append(f"{key} is not a valid JSON Schema: {exc.message}")
    return problems


OPENAPI = Kind("openapi", lambda d: (d.get("info") or {}).get("version"), _validate_openapi,
               openapi_breaks)  # fmt: skip
MCP = Kind("mcp", lambda d: d.get("version"), _validate_mcp, mcp_breaks)


def kind_of(rel: str) -> Kind | None:
    if rel.startswith("contracts/mcp/") and rel.endswith(".json"):
        return MCP
    if re.fullmatch(r"contracts/(consumed/.+/)?openapi\.ya?ml", rel):
        return OPENAPI
    if rel.startswith("contracts/consumed/") and rel.endswith(".json"):
        return MCP
    return None


def _strip_version(doc: dict, kind: Kind) -> dict:
    doc = json.loads(json.dumps(doc))
    if kind is OPENAPI:
        doc.get("info", {}).pop("version", None)
    else:
        doc.pop("version", None)
    return doc


def check_contract(rel: str, text: str, base_text: str | None) -> list[str]:
    kind = kind_of(rel)
    if kind is None:
        return []
    try:
        doc = load(text)
    except yaml.YAMLError as exc:
        return [f"{rel}: not parseable: {exc}"]
    if not isinstance(doc, dict):
        return [f"{rel}: expected a mapping at the top level"]
    problems = [f"{rel}: {p}" for p in kind.validate(doc)]
    version = semver(kind.version(doc))
    if version is None:
        problems.append(f"{rel}: version {kind.version(doc)!r} is not MAJOR.MINOR.PATCH")
    if problems or base_text is None or rel.startswith("contracts/consumed/"):
        return problems

    base = load(base_text)
    base_version = semver(kind.version(base)) if isinstance(base, dict) else None
    if base_version is None or version is None:
        return problems
    if _strip_version(base, kind) == _strip_version(doc, kind):
        if version != base_version:
            problems.append(f"{rel}: version changed {base_version} -> {version} without changes")
        return problems
    breaks = kind.breaks(base, doc)
    if version <= base_version:
        problems.append(f"{rel}: contract changed but version {'.'.join(map(str, version))} "
                        f"is not above {'.'.join(map(str, base_version))}")  # fmt: skip
    elif breaks and version[0] <= base_version[0]:
        problems.append(f"{rel}: breaking changes need a MAJOR bump "
                        f"(from {base_version[0]}.x)")  # fmt: skip
    if breaks and (version <= base_version or version[0] <= base_version[0]):
        problems += [f"{rel}: breaking: {b}" for b in breaks]
    return problems


def _git(root: Path, *args: str) -> tuple[int, str]:
    proc = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True)
    return proc.returncode, proc.stdout


def base_ref(root: Path) -> str | None:
    for ref in ("origin/main", "main"):
        code, _ = _git(root, "rev-parse", "--verify", "--quiet", ref)
        if code == 0:
            return ref
    return None


def contract_files(root: Path) -> Iterable[str]:
    for path in sorted((root / "contracts").rglob("*")):
        rel = path.relative_to(root).as_posix()
        if path.is_file() and kind_of(rel) is not None:
            yield rel


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--base", help="git ref to compare against (default: origin/main, main)")
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args(argv)
    root = args.root.resolve()
    ref = args.base or base_ref(root)

    problems: list[str] = []
    for rel in contract_files(root):
        base_text = None
        if ref:
            code, out = _git(root, "show", f"{ref}:{rel}")
            base_text = out if code == 0 else None
        problems += check_contract(rel, (root / rel).read_text(encoding="utf-8"), base_text)
    for p in problems:
        print(f"contract-check: {p}", file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
