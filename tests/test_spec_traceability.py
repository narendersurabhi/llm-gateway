"""Constitution II / NFR-003: every requirement in specs/ is covered by a tagged test."""

import socket
import tomllib
from pathlib import Path

import pytest
import spec_coverage

ROOT = Path(__file__).parent.parent


@pytest.mark.spec("NFR-003")
def test_every_requirement_has_a_test():
    uncovered, _, _, reqs = spec_coverage.check()
    assert reqs, "no requirements found in specs/*/spec.md"
    assert not uncovered, f"requirements without tests: {uncovered}"


@pytest.mark.spec("NFR-003")
def test_tests_only_cite_known_requirements():
    _, unknown, _, _ = spec_coverage.check()
    assert not unknown, f"tests cite IDs missing from specs: {unknown}"


@pytest.mark.spec("NFR-001")
def test_environment_is_uv_managed_and_locked():
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text())
    assert pyproject["tool"]["uv"]["python-preference"] == "only-managed"
    assert pyproject["project"]["requires-python"] == ">=3.11,<3.14"
    assert (ROOT / "uv.lock").exists()


@pytest.mark.spec("NFR-004")
def test_network_is_blocked_except_loopback():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1)
        with pytest.raises(ConnectionRefusedError, match="blocked in tests"):
            s.connect(("203.0.113.1", 443))  # TEST-NET-3, never routable


@pytest.mark.spec("NFR-004")
def test_live_tests_are_opt_in():
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text())
    assert "-m 'not live'" in pyproject["tool"]["pytest"]["ini_options"]["addopts"]
