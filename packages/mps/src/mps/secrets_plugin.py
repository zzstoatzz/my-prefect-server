"""prefect startup plugin: resolve `prefect-block://<name>` env sentinels.

deployments carry block NAMES in job_variables (public surface, orchestration
db) instead of deploy-time-baked plaintext; this hook swaps them for the real
values inside the flow-run process, before flow code imports. registered via
the `prefect.plugins` entry point group and gated per-deployment with
PREFECT_PLUGINS_ENABLED=true.
"""

from __future__ import annotations

import asyncio
import os
from typing import TYPE_CHECKING, Any

from prefect.plugins import register_hook

if TYPE_CHECKING:
    from prefect.plugins import HookContext

SCHEME = "prefect-block://"


async def _resolve(refs: dict[str, str]) -> dict[str, str]:
    import json

    from prefect.blocks.system import Secret

    async def one(name: str) -> str:
        secret = await Secret.aload(name)
        value = secret.get()
        return value if isinstance(value, str) else json.dumps(value)

    values = await asyncio.gather(*(one(n) for n in refs.values()))
    return dict(zip(refs.keys(), values, strict=True))


@register_hook
async def setup_environment(*, ctx: HookContext) -> Any:
    refs = {
        key: value.removeprefix(SCHEME)
        for key, value in os.environ.items()
        if value.startswith(SCHEME)
    }
    if not refs:
        return None
    from prefect.plugins import SetupResult

    env = await _resolve(refs)
    return SetupResult(env=env, note=f"resolved {len(env)} secret block ref(s)")
