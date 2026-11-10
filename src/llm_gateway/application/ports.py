"""Ports: what the use cases need from the outside world, as `typing.Protocol`s.

One port per capability, named for what the application needs (`Clock`, `DocumentIndex`),
not for the technology behind it. Adapters in `adapters/outbound/` implement them, and
`bootstrap.py` picks which adapter each port gets. Where a framework already defines a fine
port (a LangChain chat model, a LangGraph checkpointer), use it inside an adapter instead of
wrapping it again (ADR 0002).
"""

from __future__ import annotations

from typing import Protocol


class Clock(Protocol):
    def monotonic(self) -> float:
        """Seconds from an arbitrary fixed point; only differences are meaningful."""
        ...
