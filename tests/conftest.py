import os
import socket
from collections.abc import Callable, Iterator
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient
from opentelemetry.sdk._logs.export import InMemoryLogRecordExporter
from opentelemetry.sdk.metrics import Counter, Histogram
from opentelemetry.sdk.metrics.export import AggregationTemporality, InMemoryMetricReader
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from llm_gateway import ENV_PREFIX
from llm_gateway.bootstrap import create_app
from llm_gateway.infrastructure.settings import ServiceSettings
from llm_gateway.infrastructure.telemetry import ObservabilityConfig, setup_telemetry

# OpenTelemetry global providers can be installed only once per process, so the session
# shares in-memory exporters; each test starts with them empty.
SPANS = InMemorySpanExporter()
LOGS = InMemoryLogRecordExporter()
METRICS = InMemoryMetricReader(
    preferred_temporality={
        Counter: AggregationTemporality.DELTA,
        Histogram: AggregationTemporality.DELTA,
    }
)

LOOPBACK = {"127.0.0.1", "::1", "localhost", "testserver"}
_NETWORK = {"allowed": False}


class FakeClock:
    """Test adapter for the `Clock` port."""

    def __init__(self, now: float = 100.0) -> None:
        self.now = now

    def monotonic(self) -> float:
        return self.now


@pytest.fixture(autouse=True)
def _network_policy(request):
    """Only tests marked `live` may leave the machine (NFR-004)."""
    _NETWORK["allowed"] = request.node.get_closest_marker("live") is not None
    yield
    _NETWORK["allowed"] = False


@pytest.fixture(scope="session", autouse=True)
def _no_network():
    real_connect, real_connect_ex = socket.socket.connect, socket.socket.connect_ex

    def check(sock: socket.socket, address) -> None:
        if _NETWORK["allowed"] or sock.family == getattr(socket, "AF_UNIX", None):
            return
        host = address[0] if isinstance(address, tuple) else str(address)
        if host not in LOOPBACK:
            raise ConnectionRefusedError(f"network access blocked in tests: {address!r}")

    def connect(sock, address):
        check(sock, address)
        return real_connect(sock, address)

    def connect_ex(sock, address):
        check(sock, address)
        return real_connect_ex(sock, address)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(socket.socket, "connect", connect)
        mp.setattr(socket.socket, "connect_ex", connect_ex)
        yield


@pytest.fixture(scope="session", autouse=True)
def _telemetry(_no_network):
    tel = setup_telemetry(
        ObservabilityConfig(),
        console_level=None,
        span_exporter=SPANS,
        log_exporter=LOGS,
        metric_reader=METRICS,
        batch=False,
    )
    yield tel
    tel.shutdown()


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Settings read the environment; keep the developer's shell out of the tests."""
    for var in list(os.environ):
        if var.startswith((ENV_PREFIX, "OTEL_")):
            monkeypatch.delenv(var)


@pytest.fixture
def spans() -> InMemorySpanExporter:
    SPANS.clear()
    return SPANS


@pytest.fixture
def logs() -> InMemoryLogRecordExporter:
    LOGS.clear()
    return LOGS


@pytest.fixture
def collect_metrics() -> Callable[[], dict]:
    """{metric name: Metric} recorded since the previous call. Collect *before* leaving a
    `make_client` block: the app's shutdown flush drains the reader."""
    METRICS.get_metrics_data()

    def collect() -> dict:
        data = METRICS.get_metrics_data()
        found: dict = {}
        for rm in data.resource_metrics if data else []:
            for sm in rm.scope_metrics:
                for metric in sm.metrics:
                    found[metric.name] = metric
        return found

    return collect


@pytest.fixture
def make_client():
    """make_client(clock=None, **settings) -> context manager yielding (TestClient, app).
    The lifespan (telemetry) runs on enter. Server exceptions become 500 responses."""

    @contextmanager
    def factory(clock=None, **settings) -> Iterator[tuple[TestClient, object]]:
        app = create_app(ServiceSettings(**settings), clock=clock, console_logs=False)
        with TestClient(app, raise_server_exceptions=False) as client:
            yield client, app

    return factory
