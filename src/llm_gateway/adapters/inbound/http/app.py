"""Inbound HTTP adapter: routes call use cases; errors become the conventions' envelope.

Add feature routes as `APIRouter`s in this package and include them in `build_app`. Routes
translate HTTP to use-case calls and results back; policy (validation beyond the schema,
authorization decisions) belongs in the use case, so every transport applies it.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from starlette.exceptions import HTTPException as StarletteHTTPException

from ....application.health import CheckHealth
from ....application.telemetry import current_ids
from ....domain.errors import DomainError

STATUS_FOR_TYPE = {
    "invalid_request": 400,
    "unauthenticated": 401,
    "forbidden": 403,
    "not_found": 404,
    "conflict": 409,
    "rate_limited": 429,
    "internal": 500,
    "upstream_error": 502,
    "unavailable": 503,
    "timeout": 504,
}
TYPE_FOR_STATUS = {status: type_ for type_, status in STATUS_FOR_TYPE.items()} | {
    405: "invalid_request",
    413: "invalid_request",
    415: "invalid_request",
    422: "invalid_request",
}


def error_response(
    status: int, type_: str, message: str, retry_after_s: float | None = None
) -> JSONResponse:
    trace_id, _ = current_ids()
    headers = {"retry-after": str(max(1, round(retry_after_s)))} if retry_after_s else None
    if status == 401:
        headers = (headers or {}) | {"www-authenticate": "Bearer"}
    return JSONResponse(
        status_code=status,
        content={"error": {"type": type_, "message": message}, "trace_id": trace_id},
        headers=headers,
    )


def build_app(
    *,
    title: str,
    version: str,
    check_health: CheckHealth,
    lifespan: Callable[[FastAPI], AbstractAsyncContextManager[Any]] | None = None,
) -> FastAPI:
    # Stable HTTP semantic conventions: `http.server.request.duration` with bounded
    # attributes. The legacy default also records `http.host` / `http.server_name`, which
    # come from the client's Host header (unbounded cardinality). Read once per process.
    os.environ.setdefault("OTEL_SEMCONV_STABILITY_OPT_IN", "http")
    app = FastAPI(title=title, version=version, lifespan=lifespan)
    # /healthz is polled by load balancers every few seconds, so it isn't traced.
    FastAPIInstrumentor.instrument_app(
        app, excluded_urls="healthz", exclude_spans=["receive", "send"]
    )

    @app.exception_handler(DomainError)
    async def domain_error(_: Request, exc: DomainError) -> JSONResponse:
        status = STATUS_FOR_TYPE.get(exc.type, 500)
        return error_response(status, exc.type, exc.public_message, exc.retry_after_s)

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        # Field locations only: FastAPI's default echoes the offending input back.
        fields = sorted({".".join(str(p) for p in e.get("loc", ())) for e in exc.errors()})
        return error_response(422, "invalid_request", "invalid fields: " + ", ".join(fields))

    @app.exception_handler(StarletteHTTPException)
    async def http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        type_ = TYPE_FOR_STATUS.get(exc.status_code, "internal" if exc.status_code >= 500 else
                                    "invalid_request")  # fmt: skip
        return error_response(exc.status_code, type_, type_.replace("_", " "))

    @app.exception_handler(Exception)
    async def unhandled(_: Request, exc: Exception) -> JSONResponse:
        return error_response(500, "internal", "internal error")

    @app.get("/healthz")
    async def healthz() -> dict[str, Any]:
        health = check_health()
        return {"status": health.status, "version": health.version, "uptime_s": health.uptime_s}

    return app
