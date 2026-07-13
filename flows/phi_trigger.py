"""Kick a named scheduled pass on the phi bot.

phi's fly app exposes POST /api/control/trigger/{slot} (bearer control
token) for its named passes — curation, chicken-precheck, cycle,
reflection. The bot defines WHAT can run; this flow + prefect schedules
own WHEN. New phi schedules should be new deployments of this one flow
with a different slot, not new code in either repo.

Requires the `phi-control-token` secret block (the bot's CONTROL_TOKEN
fly secret).
"""

import httpx

from prefect import flow
from prefect.blocks.system import Secret

from mps.observability import configure_logfire

PHI_BASE = "https://zzstoatzz-phi.fly.dev"


@flow(name="phi-trigger", log_prints=True, timeout_seconds=120, retries=2)
async def phi_trigger(slot: str):
    """Trigger phi's named pass `slot` via the bot's control API."""
    configure_logfire("prefect-flow-phi-trigger")

    token = (await Secret.load("phi-control-token")).get()
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.post(
            f"{PHI_BASE}/api/control/trigger/{slot}",
            headers={"authorization": f"Bearer {token}"},
        )
        r.raise_for_status()
        print(f"triggered: {r.json()}")


if __name__ == "__main__":
    import asyncio
    import sys

    asyncio.run(phi_trigger(sys.argv[1] if len(sys.argv) > 1 else "cycle"))
