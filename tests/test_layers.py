"""FR-005: the ports-and-adapters dependency rule (ADR 0002)."""

import textwrap
from pathlib import Path

import check_layers
import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.spec("FR-005")
def test_this_service_follows_the_layer_rule():
    assert check_layers.check(ROOT) == []


def make_pkg(tmp_path: Path, files: dict[str, str]) -> Path:
    pkg = tmp_path / "src" / "svc"
    for rel, body in {"__init__.py": "", **files}.items():
        path = pkg / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        for parent in path.parents:
            if parent == pkg.parent:
                break
            (parent / "__init__.py").touch()
        path.write_text(textwrap.dedent(body))
    return tmp_path


@pytest.mark.spec("FR-005")
@pytest.mark.parametrize(
    ("rel", "body", "rule"),
    [
        ("domain/model.py", "import fastapi\n", "standard library"),
        ("domain/model.py", "from ..application import ports\n", "domain may only import domain"),
        ("application/run.py", "from opentelemetry.sdk.trace import TracerProvider\n",
         "OpenTelemetry API"),
        ("application/run.py", "from ..adapters.outbound import db\n", "domain and application"),
        ("adapters/inbound/http.py", "from ..outbound import db\n", "each other"),
        ("adapters/outbound/db.py", "from ...bootstrap import create_app\n", "composition root"),
        ("infrastructure/settings.py", "from ..adapters.inbound import http\n", "infrastructure"),
    ],
)  # fmt: skip
def test_violations_are_reported(tmp_path, rel, body, rule):
    root = make_pkg(tmp_path, {rel: body})
    [violation] = check_layers.check(root)
    assert rule in violation.rule


@pytest.mark.spec("FR-005")
def test_allowed_imports(tmp_path):
    root = make_pkg(
        tmp_path,
        {
            "domain/model.py": "import dataclasses\nfrom . import errors\n",
            "domain/errors.py": "",
            "application/run.py": "from opentelemetry import trace\nfrom ..domain import model\n",
            "adapters/inbound/http.py": "import fastapi\nfrom ...application import run\n",
            "adapters/outbound/db.py": "import sqlite3\nfrom ...application import run\n",
            "bootstrap.py": (
                "from .adapters.inbound import http\nfrom .adapters.outbound import db\n"
            ),
        },
    )
    assert check_layers.check(root) == []


@pytest.mark.spec("FR-005")
def test_application_allow_list_in_pyproject(tmp_path):
    root = make_pkg(tmp_path, {"application/run.py": "import pydantic\n"})
    assert len(check_layers.check(root)) == 1
    (root / "pyproject.toml").write_text('[tool.check-layers]\napplication-allow = ["pydantic"]\n')
    assert check_layers.check(root) == []
