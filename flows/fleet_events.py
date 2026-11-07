"""Alert payloads shared by fleet-health and mcp-fleet-health.

mcp-fleet-health runs with only prefect and fastmcp installed, so this module
must not import mps or another flow module.
"""

from prefect.runtime import flow_run
from prefect.settings import PREFECT_UI_URL

RUN_URL = "https://prefect-server.waow.tech/runs/flow-run/{id}"


def run_link() -> str:
    if not flow_run.id:
        return ""
    base = PREFECT_UI_URL.value()
    return (
        f"{base.rstrip('/')}/runs/flow-run/{flow_run.id}"
        if base
        else RUN_URL.format(id=flow_run.id)
    )


def unhealthy_payload(unhealthy: list[str]) -> dict[str, object]:
    """`fleet unhealthy -> discord` renders `summary`; discord caps a message at 2000."""
    link = run_link()
    tail = f"\nrun: {link}" if link else ""
    return {"unhealthy": unhealthy, "summary": "\n".join(unhealthy)[: 1800 - len(tail)] + tail}
