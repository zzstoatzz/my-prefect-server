"""Observability setup for flow processes."""

from __future__ import annotations

import os
from typing import Any

_configured = False


def configure_logfire(service_name: str, **kwargs: Any) -> None:
    """Configure Logfire if available, without requiring a token on day one."""
    global _configured
    if _configured:
        return

    try:
        import logfire

        logfire.configure(
            service_name=service_name,
            environment=os.environ.get("PREFECT_PROFILE", "production"),
            send_to_logfire="if-token-present",
            **kwargs,
        )
        logfire.instrument_httpx()
        _configured = True
    except Exception:
        return
