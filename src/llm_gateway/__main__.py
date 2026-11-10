"""Entry point: serve the HTTP adapter with uvicorn on $PORT."""

from __future__ import annotations

import os

import uvicorn

from . import DEFAULT_PORT
from .bootstrap import create_app


def main() -> None:
    uvicorn.run(
        create_app(),
        host="0.0.0.0",  # inside a container; the load balancer / port mapping is the boundary
        port=int(os.environ.get("PORT", DEFAULT_PORT)),
        log_config=None,  # uvicorn logs go through the root logger (JSON + OTLP)
        access_log=False,  # requests are covered by HTTP server spans and metrics
        proxy_headers=True,
        forwarded_allow_ips="*",
        timeout_graceful_shutdown=30,
    )


if __name__ == "__main__":
    main()
