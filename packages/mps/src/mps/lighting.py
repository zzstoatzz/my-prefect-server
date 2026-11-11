"""Run a lighting plan through the smart-home MCP, then verify readback."""

import asyncio
import importlib
import json
import os
import sys
from collections.abc import Callable
from functools import partial
from pathlib import Path
from typing import Any, Literal

from mps.home_lighting import (
    LightChange,
    LightingCall,
    expected_states,
    lighting_calls,
    plan_changes,
    readback_mismatches,
)

Inventory = dict[str, Any]
Planner = Callable[[Inventory, Inventory, Inventory], list[LightingCall]]


async def apply_lighting(state: Literal["home", "away"]) -> int:
    return await apply_plan(partial(lighting_calls, state), needs_scenes=state == "home")


async def apply_changes(changes: list[LightChange]) -> int:
    return await apply_plan(
        partial(plan_changes, changes),
        needs_scenes=any(change.scene is not None for change in changes),
        every_light=False,
    )


async def apply_plan(plan: Planner, *, needs_scenes: bool, every_light: bool = True) -> int:
    os.environ.update(json.loads(Path(os.environ["PRESENCE_HUE_ENV_FILE"]).read_text()))
    from fastmcp import Client

    lights_mcp = importlib.import_module("smart_home.lights.server").lights_mcp

    async with asyncio.timeout(120), Client(lights_mcp) as client:

        async def write(tool: str, arguments: dict[str, Any]) -> None:
            # A disconnected bulb must not prevent commands to the remaining lights.
            # Readback below decides success, including possibly-applied Hue errors.
            result = await client.call_tool(tool, arguments, raise_on_error=False)
            if result.is_error:
                detail = " ".join(getattr(c, "text", "") for c in result.content or []).strip()
                print(
                    f"Command not confirmed: {tool} {arguments.get('target', '')}: {detail[:500]}",
                    file=sys.stderr,
                )

        async def read(tool: str) -> dict[str, Any]:
            result = await client.call_tool(tool, {})
            if result.structured_content is None:
                raise ValueError(f"{tool} returned no structured content")
            return result.structured_content

        rooms = await read("read_rooms")
        lights = await read("read_lights")
        scenes = await read("read_scenes") if needs_scenes else {}
        calls = plan(rooms, lights, scenes)
        expected = expected_states(calls, lights, scenes, every_light=every_light)
        for call in calls:
            if call.tool == "activate_scene":
                # Clear ad-hoc effects before recalling the saved scene.
                for light_id in rooms[call.arguments["room"]]["lights"]:
                    if "no_effect" in lights[light_id]["supported_effects"]:
                        await write(
                            "set_light", {"target": light_id, "state": {"effect": "no_effect"}}
                        )
            await write(call.tool, call.arguments)
        for _attempt in range(4):
            await asyncio.sleep(1)
            observed = await read("read_lights")
            mismatches = readback_mismatches(expected, observed)
            if not mismatches:
                return len(expected)
        raise ValueError(f"Lighting readback did not match: {mismatches}")
