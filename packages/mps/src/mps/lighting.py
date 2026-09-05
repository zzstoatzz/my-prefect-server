"""Run the fixed presence preset through the smart-home MCP, then verify readback."""

import asyncio
import importlib
import json
import os
import sys
from pathlib import Path
from typing import Any, Literal

from mps.home_lighting import expected_states, lighting_calls, readback_mismatches


async def apply_lighting(state: Literal["home", "away"]) -> int:
    os.environ.update(json.loads(Path(os.environ["PRESENCE_HUE_ENV_FILE"]).read_text()))
    from fastmcp import Client

    lights_mcp = importlib.import_module("smart_home.lights.server").lights_mcp

    async with asyncio.timeout(120), Client(lights_mcp) as client:

        async def write(tool: str, arguments: dict[str, Any]) -> None:
            # A disconnected bulb must not prevent commands to the remaining lights.
            # Readback below decides success, including possibly-applied Hue errors.
            result = await client.call_tool(tool, arguments, raise_on_error=False)
            if result.is_error:
                print(
                    f"Command not confirmed: {tool} {arguments.get('target', '')}", file=sys.stderr
                )

        async def read(tool: str) -> dict[str, Any]:
            result = await client.call_tool(tool, {})
            if result.structured_content is None:
                raise ValueError(f"{tool} returned no structured content")
            return result.structured_content

        rooms = await read("read_rooms")
        lights = await read("read_lights")
        scenes = await read("read_scenes") if state == "home" else {}
        calls = lighting_calls(state, rooms, lights, scenes)
        expected = expected_states(calls, lights, scenes)
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
