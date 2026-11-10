"""OpenTelemetry API helpers for use cases (ADR 0002: the API is allowed here, the SDK is not).

Calls are no-ops until infrastructure installs providers, so use cases stay testable.
"""

from __future__ import annotations

import traceback

from opentelemetry import metrics, trace
from opentelemetry.trace import Span, Status, StatusCode

SCOPE = __name__.split(".")[0]
tracer = trace.get_tracer(SCOPE)
meter = metrics.get_meter(SCOPE)

SECONDS_BUCKETS = [0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 120, 300]


def exception_attributes(exc: BaseException) -> dict[str, str]:
    """Type and the innermost code frames only: messages can carry user content."""
    return {
        "exception.type": type(exc).__qualname__,
        "exception.stacktrace": "".join(traceback.format_tb(exc.__traceback__, limit=-10)),
    }


def mark_error(span: Span, exc: BaseException) -> None:
    attrs = exception_attributes(exc)
    span.add_event("exception", attrs)
    span.set_status(Status(StatusCode.ERROR, attrs["exception.type"]))


def current_ids() -> tuple[str, str]:
    """(trace_id, span_id) of the active span as hex, or zeros when there is none."""
    ctx = trace.get_current_span().get_span_context()
    if not ctx.is_valid:
        return "0" * 32, "0" * 16
    return trace.format_trace_id(ctx.trace_id), trace.format_span_id(ctx.span_id)
