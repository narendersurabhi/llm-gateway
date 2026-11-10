import io
import json
import logging
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk._logs.export import ConsoleLogRecordExporter
from opentelemetry.sdk.metrics.export import ConsoleMetricExporter
from opentelemetry.sdk.trace.export import ConsoleSpanExporter
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from pydantic import ValidationError

from llm_gateway import SERVICE_NAME, __version__
from llm_gateway.infrastructure.telemetry import (
    JsonConsoleFormatter,
    ObservabilityConfig,
    Telemetry,
    TelemetryExporter,
    build_log_exporter,
    build_metric_exporter,
    build_providers,
    build_resource,
    build_span_exporter,
)

log = logging.getLogger("test.telemetry")
# Bounded attributes the HTTP server metric may carry: never raw paths or IDs.
HTTP_METRIC_KEYS = {
    "http.request.method", "http.response.status_code", "http.route", "url.scheme",
    "network.protocol.name", "network.protocol.version", "server.address", "server.port",
    "error.type",
}  # fmt: skip


def add_item_route(app):
    def get_item(item_id: str):
        log.info("item fetched", extra={"item.kind": "widget"})
        return {"id": item_id}

    app.add_api_route("/v1/items/{item_id}", get_item)


# --- FR-003: request telemetry --------------------------------------------------------------


@pytest.mark.spec("FR-003")
def test_request_span_and_correlated_log(make_client, spans, logs):
    with make_client() as (client, app):
        add_item_route(app)
        assert client.get("/v1/items/abc123").status_code == 200
    [server] = [s for s in spans.get_finished_spans() if s.parent is None]
    assert server.name == "GET /v1/items/{item_id}"
    assert server.attributes["http.route"] == "/v1/items/{item_id}"
    [rec] = [r.log_record for r in logs.get_finished_logs() if r.log_record.body == "item fetched"]
    assert rec.trace_id == server.context.trace_id
    assert rec.attributes["item.kind"] == "widget"


@pytest.mark.spec("FR-003")
def test_http_metric_is_bounded(make_client, collect_metrics):
    with make_client() as (client, app):
        add_item_route(app)
        collect_metrics()
        client.get("/v1/items/abc123")
        client.get("/v1/items/def456")
        metrics = collect_metrics()
    assert "http.server.request.duration" in metrics, sorted(metrics)  # stable semconv
    assert "http.server.duration" not in metrics  # legacy: carries the Host header
    points = list(metrics["http.server.request.duration"].data.data_points)
    assert points and {p.attributes["http.route"] for p in points} == {"/v1/items/{item_id}"}
    for p in points:
        assert set(p.attributes) <= HTTP_METRIC_KEYS, dict(p.attributes)
        assert not any(v in str(dict(p.attributes)) for v in ("abc123", "def456"))


@pytest.mark.spec("FR-003")
def test_healthz_is_not_traced(make_client, spans):
    with make_client() as (client, _):
        client.get("/healthz")
    assert spans.get_finished_spans() == ()


@pytest.mark.spec("FR-003")
def test_console_logs_are_json_with_trace_context():
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonConsoleFormatter())
    logger = logging.getLogger("test.console")
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    try:
        with trace.get_tracer("t").start_as_current_span("s") as span:
            logger.info("hello", extra={"outcome": "success"})
        logger.warning("outside")
    finally:
        logger.removeHandler(handler)
    inside, outside = (json.loads(line) for line in stream.getvalue().splitlines())
    ctx = span.get_span_context()
    assert inside["message"] == "hello" and inside["level"] == "INFO"
    assert inside["trace_id"] == trace.format_trace_id(ctx.trace_id)
    assert inside["span_id"] == trace.format_span_id(ctx.span_id)
    assert inside["outcome"] == "success"
    assert outside["trace_id"] == "0" * 32


# --- FR-004: configuration and lifecycle ----------------------------------------------------


@pytest.mark.spec("FR-004")
def test_exporter_selection():
    console = ObservabilityConfig(
        traces_exporter="console", logs_exporter="console", metrics_exporter="console"
    )
    assert isinstance(build_span_exporter(console), ConsoleSpanExporter)
    assert isinstance(build_log_exporter(console), ConsoleLogRecordExporter)
    assert isinstance(build_metric_exporter(console), ConsoleMetricExporter)
    otlp = ObservabilityConfig(
        traces_exporter="otlp", logs_exporter="otlp", metrics_exporter="otlp",
        otlp_endpoint="http://collector:4318/",
    )  # fmt: skip
    assert isinstance(build_span_exporter(otlp), OTLPSpanExporter)
    assert isinstance(build_log_exporter(otlp), OTLPLogExporter)
    assert isinstance(build_metric_exporter(otlp), OTLPMetricExporter)
    assert build_span_exporter(otlp)._endpoint == "http://collector:4318/v1/traces"
    none = ObservabilityConfig(
        traces_exporter="none", logs_exporter="none", metrics_exporter="none"
    )
    assert build_span_exporter(none) is build_log_exporter(none) is build_metric_exporter(none)


@pytest.mark.spec("FR-004")
def test_standard_env_vars(monkeypatch):
    monkeypatch.setenv("OTEL_TRACES_EXPORTER", "otlp")
    monkeypatch.setenv("OTEL_LOGS_EXPORTER", "console")
    monkeypatch.setenv("OTEL_SERVICE_NAME", "renamed")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://env-collector:4318")
    cfg = ObservabilityConfig()
    assert cfg.traces_exporter is TelemetryExporter.OTLP
    assert cfg.logs_exporter is TelemetryExporter.CONSOLE
    assert cfg.metrics_exporter is TelemetryExporter.NONE
    assert cfg.service_name == "renamed"
    assert build_span_exporter(cfg)._endpoint == "http://env-collector:4318/v1/traces"


@pytest.mark.spec("FR-004")
def test_resource_attributes():
    attrs = build_resource(ObservabilityConfig(environment="prod")).attributes
    assert attrs["service.name"] == SERVICE_NAME
    assert attrs["service.version"] == __version__
    assert attrs["deployment.environment.name"] == "prod"


@pytest.mark.spec("FR-004")
@pytest.mark.parametrize(
    "kwargs", [{"traces_exporter": "jaeger"}, {"sample_ratio": 1.5}, {"log_level": "LOUD"}]
)
def test_invalid_config_rejected(kwargs):
    with pytest.raises(ValidationError):
        ObservabilityConfig(**kwargs)


@pytest.mark.spec("FR-004")
def test_owned_providers_flushed_on_shutdown():
    exporter = InMemorySpanExporter()
    tp, lp, mp = build_providers(ObservabilityConfig(), span_exporter=exporter, batch=True)
    tp.get_tracer("t").start_span("pending").end()
    Telemetry(tracer_provider=tp, logger_provider=lp, meter_provider=mp,
              owns_providers=True).shutdown()  # fmt: skip
    assert [s.name for s in exporter.get_finished_spans()] == ["pending"]
    assert exporter._stopped


@pytest.mark.spec("FR-004")
def test_app_lifespan_shuts_telemetry_down(make_client, monkeypatch):
    from llm_gateway import bootstrap

    calls = []
    real = bootstrap.setup_telemetry

    def recording(*args, **kwargs):
        tel = real(*args, **kwargs)
        inner = tel.shutdown
        tel.shutdown = lambda: calls.append("shutdown") or inner()
        return tel

    monkeypatch.setattr(bootstrap, "setup_telemetry", recording)
    with make_client() as (client, _):
        client.get("/healthz")
        assert calls == []
    assert calls == ["shutdown"]


# --- NFR-002: telemetry never breaks the service --------------------------------------------

SCRIPT = textwrap.dedent(
    """
    import json, logging
    from fastapi.testclient import TestClient
    from llm_gateway.bootstrap import create_app

    app = create_app(console_logs=False)

    def work():
        logging.getLogger("x").info("did work")
        return {"ok": True}

    app.add_api_route("/work", work)
    with TestClient(app) as client:
        resp = client.get("/work")
    print(json.dumps({"status": resp.status_code}))
    """
)


@pytest.mark.spec("NFR-002")
def test_unreachable_collector_does_not_affect_requests():
    env = {
        "PATH": "/usr/bin:/bin",
        "OTEL_TRACES_EXPORTER": "otlp",
        "OTEL_LOGS_EXPORTER": "otlp",
        "OTEL_METRICS_EXPORTER": "otlp",
        "OTEL_EXPORTER_OTLP_ENDPOINT": "http://127.0.0.1:9",  # nothing listens
        "LLM_GATEWAY_OTLP_TIMEOUT_S": "2",
    }
    start = time.monotonic()
    proc = subprocess.run([sys.executable, "-c", SCRIPT], capture_output=True, text=True,
                          env=env, timeout=120, cwd=Path(__file__).parent.parent)  # fmt: skip
    assert proc.returncode == 0, proc.stderr[-3000:]
    assert json.loads(proc.stdout.strip().splitlines()[-1]) == {"status": 200}
    assert time.monotonic() - start < 60
