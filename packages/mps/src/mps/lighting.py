"""Run a lighting plan through the smart-home MCP, then verify readback."""

import asyncio
import importlib
import json
import os
import sys
from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from mps.home_lighting import (
    LightChange,
    LightingCall,
    expected_states,
    lighting_calls,
    plan_changes,
    readback_mismatches,
)

if TYPE_CHECKING:
    from fastmcp import Client

Inventory = dict[str, Any]
Planner = Callable[[Inventory, Inventory, Inventory], list[LightingCall]]


@dataclass
class WindDownResult:
    accepted: list[str] = field(default_factory=list)
    unreachable: list[str] = field(default_factory=list)
    unconfirmed: list[str] = field(default_factory=list)


@asynccontextmanager
async def hue_client() -> AsyncGenerator["Client"]:
    os.environ.update(json.loads(Path(os.environ["PRESENCE_HUE_ENV_FILE"]).read_text()))
    from fastmcp import Client

    lights_mcp = importlib.import_module("smart_home.lights.server").lights_mcp
    async with asyncio.timeout(120), Client(lights_mcp) as client:
        yield client


async def read_inventory(client: "Client", tool: str) -> Inventory:
    result = await client.call_tool(tool, {})
    if result.structured_content is None:
        raise ValueError(f"{tool} returned no structured content")
    return result.structured_content


async def write_light(client: "Client", tool: str, arguments: dict[str, Any]) -> bool:
    result = await client.call_tool(tool, arguments, raise_on_error=False)
    if result.is_error:
        detail = " ".join(getattr(c, "text", "") for c in result.content or []).strip()
        print(
            f"Command not confirmed: {tool} {arguments.get('target', '')}: {detail[:500]}",
            file=sys.stderr,
        )
    return not result.is_error


async def apply_wind_down(end: datetime, brightness: float) -> WindDownResult:
    result = WindDownResult()
    async with hue_client() as client:
        lights = await read_inventory(client, "read_lights")
        if not lights:
            raise ValueError("The Hue bridge returned no lights")
        for light_id, light in lights.items():
            if light["connectivity"] != "connected":
                result.unreachable.append(light["name"])
                continue
            state = light["state"]
            level = state["brightness"]
            minimum = light["capabilities"].get("minimum_brightness")
            target = max(brightness, minimum or 0)
            if state["on"] is not True or level is None or level <= target:
                continue
            remaining = (end - datetime.now(UTC)).total_seconds()
            if remaining <= 0:
                break
            accepted = await write_light(
                client,
                "set_light",
                {
                    "target": light_id,
                    "state": {"brightness": target, "transition_seconds": remaining},
                },
            )
            (result.accepted if accepted else result.unconfirmed).append(light["name"])
    return result


async def apply_lighting(state: Literal["home", "away"]) -> int:
    return await apply_plan(partial(lighting_calls, state), needs_scenes=state == "home")


async def apply_changes(changes: list[LightChange]) -> int:
    return await apply_plan(
        partial(plan_changes, changes),
        needs_scenes=any(change.scene is not None for change in changes),
        every_light=False,
    )


async def apply_plan(plan: Planner, *, needs_scenes: bool, every_light: bool = True) -> int:
    async with hue_client() as client:
        rooms = await read_inventory(client, "read_rooms")
        lights = await read_inventory(client, "read_lights")
        scenes = await read_inventory(client, "read_scenes") if needs_scenes else {}
        calls = plan(rooms, lights, scenes)
        expected = expected_states(calls, lights, scenes, every_light=every_light)
        for call in calls:
            if call.tool == "activate_scene":
                # Clear ad-hoc effects before recalling the saved scene.
                for light_id in rooms[call.arguments["room"]]["lights"]:
                    if "no_effect" in lights[light_id]["supported_effects"]:
                        await write_light(
                            client,
                            "set_light",
                            {"target": light_id, "state": {"effect": "no_effect"}},
                        )
            await write_light(client, call.tool, call.arguments)
        for _attempt in range(4):
            await asyncio.sleep(1)
            observed = await read_inventory(client, "read_lights")
            mismatches = readback_mismatches(expected, observed)
            if not mismatches:
                return len(expected)
        raise ValueError(f"Lighting readback did not match: {mismatches}")
