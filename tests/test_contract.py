"""FR-006: the provided contract is valid, versioned and compatible with main (ADR 0003).
Conformance of the adapter's responses to the contract is in test_http.py."""

from pathlib import Path

import contract_check
import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.spec("FR-006")
def test_contracts_pass_the_check():
    assert contract_check.main(["--root", str(ROOT)]) == 0


@pytest.mark.spec("FR-006")
def test_contract_documents_health_and_error_envelope():
    doc = yaml.safe_load((ROOT / "contracts" / "openapi.yaml").read_text())
    assert contract_check.semver(doc["info"]["version"])
    assert "/healthz" in doc["paths"]
    assert {"Health", "Error"} <= set(doc["components"]["schemas"])
