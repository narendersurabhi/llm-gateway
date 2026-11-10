"""OpenTelemetry SDK setup: providers, exporters, JSON console logs, shutdown.

Conventions, Telemetry: exporters `none` / `console` / `otlp` from the standard
`OTEL_*_EXPORTER` variables, OTLP endpoint from `OTEL_EXPORTER_OTLP_*`, `service.name` from
`OTEL_SERVICE_NAME`. Telemetry failures never fail a request.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum

from opentelemetry import _logs, metrics, trace
from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.logging.handler import LoggingHandler
from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.sdk._logs.export import (
    BatchLogRecordProcessor,
    ConsoleLogRecordExporter,
    LogRecordExporter,
    SimpleLogRecordProcessor,
)
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import (
    ConsoleMetricExporter,
    MetricExporter,
    MetricReader,
    PeriodicExportingMetricReader,
)
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    ConsoleSpanExporter,
    SimpleSpanProcessor,
    SpanExporter,
)
from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased
from pydantic import BaseModel, ConfigDict, Field

from .. import ENV_PREFIX, SERVICE_NAME, __version__
from ..application.telemetry import current_ids


class TelemetryExporter(StrEnum):
    NONE = "none"
    CONSOLE = "console"
    OTLP = "otlp"


def _env(var: str, default: str) -> str:
    return os.environ.get(var, "").strip() or default


class ObservabilityConfig(BaseModel):
    """Invalid values fail validation, so a typo stops startup instead of dropping telemetry."""

    model_config = ConfigDict(validate_default=True)

    enabled: bool = True
    service_name: str = Field(default_factory=lambda: _env("OTEL_SERVICE_NAME", SERVICE_NAME))
    environment: str = Field(default_factory=lambda: _env(f"{ENV_PREFIX}ENVIRONMENT", "dev"))
    traces_exporter: TelemetryExporter = Field(
        default_factory=lambda: _env("OTEL_TRACES_EXPORTER", "none").lower()
    )
    logs_exporter: TelemetryExporter = Field(
        default_factory=lambda: _env("OTEL_LOGS_EXPORTER", "none").lower()
    )
    metrics_exporter: TelemetryExporter = Field(
        default_factory=lambda: _env("OTEL_METRICS_EXPORTER", "none").lower()
    )
    # None lets each OTLP exporter honour OTEL_EXPORTER_OTLP_[<SIGNAL>_]ENDPOINT itself.
    otlp_endpoint: str | None = None
    otlp_timeout_s: float = Field(
        default_factory=lambda: float(_env(f"{ENV_PREFIX}OTLP_TIMEOUT_S", "10")), gt=0
    )
    metric_export_interval_s: float = Field(
        default_factory=lambda: float(_env(f"{ENV_PREFIX}METRIC_EXPORT_INTERVAL_S", "60")), gt=0
    )
    sample_ratio: float = Field(
        default_factory=lambda: float(_env(f"{ENV_PREFIX}TRACE_SAMPLE_RATIO", "1.0")), ge=0, le=1
    )
    log_level: str = Field(
        default_factory=lambda: _env(f"{ENV_PREFIX}LOG_LEVEL", "INFO").upper(),
        pattern="^(DEBUG|INFO|WARNING|ERROR|CRITICAL)$",
    )


_STANDARD_RECORD_KEYS = set(logging.makeLogRecord({}).__dict__) | {"message", "asctime"}


class JsonConsoleFormatter(logging.Formatter):
    """One JSON object per line with trace context and `extra=` attributes."""

    def format(self, record: logging.LogRecord) -> str:
        trace_id, span_id = current_ids()
        payload = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "trace_id": trace_id,
            "span_id": span_id,
        }
        for key, value in record.__dict__.items():
            if key not in _STANDARD_RECORD_KEYS and not key.startswith("_"):
                payload[key] = value
        return json.dumps(payload, default=str)


def build_resource(cfg: ObservabilityConfig) -> Resource:
    return Resource.create(
        {
            "service.name": cfg.service_name,
            "service.version": __version__,
            "deployment.environment.name": cfg.environment,
        }
    )


def _otlp_url(cfg: ObservabilityConfig, signal: str) -> str | None:
    return f"{cfg.otlp_endpoint.rstrip('/')}/v1/{signal}" if cfg.otlp_endpoint else None


def build_span_exporter(cfg: ObservabilityConfig) -> SpanExporter | None:
    if cfg.traces_exporter is TelemetryExporter.CONSOLE:
        return ConsoleSpanExporter()
    if cfg.traces_exporter is TelemetryExporter.OTLP:
        return OTLPSpanExporter(endpoint=_otlp_url(cfg, "traces"), timeout=cfg.otlp_timeout_s)
    return None


def build_log_exporter(cfg: ObservabilityConfig) -> LogRecordExporter | None:
    if cfg.logs_exporter is TelemetryExporter.CONSOLE:
        return ConsoleLogRecordExporter()
    if cfg.logs_exporter is TelemetryExporter.OTLP:
        return OTLPLogExporter(endpoint=_otlp_url(cfg, "logs"), timeout=cfg.otlp_timeout_s)
    return None


def build_metric_exporter(cfg: ObservabilityConfig) -> MetricExporter | None:
    if cfg.metrics_exporter is TelemetryExporter.CONSOLE:
        return ConsoleMetricExporter()
    if cfg.metrics_exporter is TelemetryExporter.OTLP:
        return OTLPMetricExporter(endpoint=_otlp_url(cfg, "metrics"), timeout=cfg.otlp_timeout_s)
    return None


def build_providers(
    cfg: ObservabilityConfig,
    span_exporter: SpanExporter | None = None,
    log_exporter: LogRecordExporter | None = None,
    metric_reader: MetricReader | None = None,
    batch: bool = True,
) -> tuple[TracerProvider, LoggerProvider, MeterProvider]:
    """Build (not install) providers. Explicit exporters/readers override the configured ones."""
    resource = build_resource(cfg)
    tracer_provider = TracerProvider(
        resource=resource, sampler=ParentBased(TraceIdRatioBased(cfg.sample_ratio))
    )
    logger_provider = LoggerProvider(resource=resource)

    span_exporter = span_exporter or build_span_exporter(cfg)
    if span_exporter is not None:
        processor = BatchSpanProcessor if batch else SimpleSpanProcessor
        tracer_provider.add_span_processor(processor(span_exporter))

    log_exporter = log_exporter or build_log_exporter(cfg)
    if log_exporter is not None:
        processor = BatchLogRecordProcessor if batch else SimpleLogRecordProcessor
        logger_provider.add_log_record_processor(processor(log_exporter))

    if metric_reader is None and (metric_exporter := build_metric_exporter(cfg)) is not None:
        metric_reader = PeriodicExportingMetricReader(
            metric_exporter,
            export_interval_millis=cfg.metric_export_interval_s * 1000,
            export_timeout_millis=cfg.otlp_timeout_s * 1000,
        )
    meter_provider = MeterProvider(
        resource=resource,
        metric_readers=[metric_reader] if metric_reader is not None else [],
        shutdown_on_exit=False,  # Telemetry.shutdown() owns this
    )
    return tracer_provider, logger_provider, meter_provider


@dataclass
class Telemetry:
    """Handle returned by `setup_telemetry`. Call `shutdown()` exactly once on exit."""

    tracer_provider: TracerProvider | None = None
    logger_provider: LoggerProvider | None = None
    meter_provider: MeterProvider | None = None
    owns_providers: bool = False
    handlers: list[logging.Handler] = field(default_factory=list)

    def shutdown(self) -> None:
        root = logging.getLogger()
        for handler in self.handlers:
            root.removeHandler(handler)
            handler.close()
        self.handlers.clear()
        # MeterProvider.shutdown() runs a final collection, so short-lived tasks still export.
        for provider in (self.tracer_provider, self.logger_provider, self.meter_provider):
            if provider is None:
                continue
            try:
                if self.owns_providers:
                    provider.shutdown()
                else:
                    provider.force_flush()
            except Exception:  # telemetry must never break the service
                logging.getLogger(__name__).debug("telemetry shutdown failed", exc_info=True)


def _installed_sdk_providers() -> tuple[TracerProvider, LoggerProvider, MeterProvider] | None:
    tp, lp = trace.get_tracer_provider(), _logs.get_logger_provider()
    mp = metrics.get_meter_provider()
    if (
        isinstance(tp, TracerProvider)
        and isinstance(lp, LoggerProvider)
        and isinstance(mp, MeterProvider)
    ):
        return tp, lp, mp
    return None


def setup_telemetry(
    cfg: ObservabilityConfig,
    console_level: int | None = logging.INFO,
    console_handler: logging.Handler | None = None,
    span_exporter: SpanExporter | None = None,
    log_exporter: LogRecordExporter | None = None,
    metric_reader: MetricReader | None = None,
    batch: bool = True,
) -> Telemetry:
    """Install global providers and logging handlers.

    Reuses providers already installed in this process (OpenTelemetry allows the global
    providers to be set only once); `shutdown()` then only flushes them.
    """
    telemetry = Telemetry()
    root = logging.getLogger()
    levels = []

    if console_level is not None:
        handler = console_handler or logging.StreamHandler()
        handler.setLevel(console_level)
        handler.setFormatter(JsonConsoleFormatter())
        root.addHandler(handler)
        telemetry.handlers.append(handler)
        levels.append(console_level)

    if cfg.enabled:
        existing = _installed_sdk_providers()
        if existing:
            tp, lp, mp = existing
        else:
            tp, lp, mp = build_providers(
                cfg, span_exporter, log_exporter, metric_reader, batch=batch
            )
            trace.set_tracer_provider(tp)
            _logs.set_logger_provider(lp)
            metrics.set_meter_provider(mp)
            telemetry.owns_providers = True
        telemetry.tracer_provider, telemetry.logger_provider, telemetry.meter_provider = tp, lp, mp

        otel_level = logging.getLevelName(cfg.log_level)
        levels.append(otel_level)
        if not any(isinstance(h, LoggingHandler) for h in root.handlers):
            otel_handler = LoggingHandler(level=otel_level, logger_provider=lp)
            root.addHandler(otel_handler)
            telemetry.handlers.append(otel_handler)

    if levels:
        root.setLevel(min(levels))
    # The OTLP exporters log every failed retry; keep an unreachable collector quiet.
    for noisy in ("opentelemetry.exporter.otlp", "urllib3.connectionpool"):
        logging.getLogger(noisy).setLevel(logging.ERROR)
    return telemetry
