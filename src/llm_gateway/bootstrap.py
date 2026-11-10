"""Composition root (ADR 0002): the only module that chooses adapters and wires use cases.

Tests and alternative entry points (a queue worker, a CLI) build the service here too,
passing fakes for any port they want to replace.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from . import SERVICE_NAME, __version__
from .adapters.inbound.http.app import build_app
from .adapters.outbound.system_clock import SystemClock
from .application.health import CheckHealth
from .application.ports import Clock
from .infrastructure.settings import ServiceSettings
from .infrastructure.telemetry import ObservabilityConfig, setup_telemetry


def create_app(
    settings: ServiceSettings | None = None,
    *,
    clock: Clock | None = None,
    console_logs: bool = True,
) -> FastAPI:
    settings = settings or ServiceSettings()
    check_health = CheckHealth(clock or SystemClock(), __version__)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        telemetry = setup_telemetry(
            ObservabilityConfig(environment=settings.environment, log_level=settings.log_level),
            console_level=logging.INFO if console_logs else None,
        )
        try:
            yield
        finally:
            telemetry.shutdown()

    return build_app(
        title=SERVICE_NAME, version=__version__, check_health=check_health, lifespan=lifespan
    )
