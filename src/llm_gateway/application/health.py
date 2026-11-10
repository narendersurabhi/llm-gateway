"""Use case: report liveness. The smallest example of inbound adapter -> use case -> port."""

from __future__ import annotations

from dataclasses import dataclass

from .ports import Clock


@dataclass(frozen=True)
class Health:
    status: str
    version: str
    uptime_s: float


class CheckHealth:
    def __init__(self, clock: Clock, version: str) -> None:
        self._clock = clock
        self._version = version
        self._started = clock.monotonic()

    def __call__(self) -> Health:
        return Health("ok", self._version, round(self._clock.monotonic() - self._started, 3))
