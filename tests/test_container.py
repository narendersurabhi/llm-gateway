"""FR-007: the container image and local stack (nothing is built here; see the README)."""

import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
DOCKERFILE = (ROOT / "Dockerfile").read_text()


def stages() -> list[str]:
    return re.split(r"(?m)^FROM ", DOCKERFILE)[1:]


@pytest.mark.spec("FR-007", "NFR-001")
def test_two_stage_locked_build_with_managed_python():
    build, runtime = stages()
    assert "uv sync --locked --no-dev" in build and "UV_PYTHON_PREFERENCE=only-managed" in build
    assert "COPY --from=build /python /python" in runtime
    assert "COPY --from=build --chown=app:app /app/.venv /app/.venv" in runtime


@pytest.mark.spec("FR-007")
def test_runtime_non_root_with_healthcheck_and_port():
    _, runtime = stages()
    assert re.search(r"(?m)^USER app$", runtime)
    assert re.search(r"(?ms)^HEALTHCHECK .*?/healthz", runtime)
    assert re.search(r"(?m)^\s+PORT=8080$", runtime)
    assert re.search(r'(?m)^CMD \["llm-gateway"\]$', runtime)


@pytest.mark.spec("FR-007")
def test_base_images_pinned():
    for image in re.findall(r"(?m)^FROM (\S+)", DOCKERFILE):
        _, _, tag = image.partition(":")
        assert tag and tag != "latest" and re.search(r"\d", tag), image


@pytest.mark.spec("FR-007")
def test_compose_exports_all_signals_to_lgtm():
    services = yaml.safe_load((ROOT / "docker-compose.yml").read_text())["services"]
    env = services["llm-gateway"]["environment"]
    for signal in ("TRACES", "LOGS", "METRICS"):
        assert env[f"OTEL_{signal}_EXPORTER"] == "otlp"
    assert env["OTEL_EXPORTER_OTLP_ENDPOINT"] == "http://otel-lgtm:4318"
    assert re.fullmatch(r"grafana/otel-lgtm:\d+\.\d+\.\d+", services["otel-lgtm"]["image"])


@pytest.mark.spec("FR-007")
def test_entry_point_listens_on_port(monkeypatch):
    from llm_gateway import __main__ as entry

    seen = {}
    monkeypatch.setattr(entry.uvicorn, "run", lambda app, **kw: seen.update(kw))
    monkeypatch.setattr(entry, "create_app", lambda: "app")
    monkeypatch.setenv("PORT", "9123")
    entry.main()
    assert seen["host"] == "0.0.0.0" and seen["port"] == 9123
