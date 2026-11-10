"""Outbound adapter for the `Clock` port."""

from __future__ import annotations

import time


class SystemClock:
    def monotonic(self) -> float:
        return time.monotonic()
