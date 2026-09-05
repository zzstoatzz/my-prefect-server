"""Run the fixed presence preset through the smart-home MCP, then verify readback."""

import asyncio
import json
import os
import sys
from pathlib import Path

from mps.home_lighting import lighting_calls


async def main() -> None:
    os.environ.update(json.loads(Path(os.environ["PRESENCE_HUE_ENV_FILE"]).read_text()))
    from fastmcp import Client
    from smart_home.lights.server import lights_mcp

    state = sys.argv[1]
    if state not in ("home", "away"):
        raise ValueError("Expected home or away")
    async with Client(lights_mcp) as client:

        async def read(tool):
            result = await client.call_tool(tool, {})
            if result.structured_content is None:
                raise ValueError(f"{tool} returned no structured content")
            return result.structured_content

        rooms = await read("read_rooms")
        lights = await read("read_lights")
        scenes = await read("read_scenes") if state == "home" else {}
        calls = lighting_calls(state, rooms, lights, scenes)
        expected = {}
        for call in calls:
            if call.tool == "activate_scene":
                scene = scenes[call.arguments["scene"]]
                for entry in scene["actions"]:
                    light_id = entry["target"]["rid"]
                    action = entry["action"]
                    desired = {"on": action.get("on", {}).get("on", True)}
                    if "dimming" in action:
                        desired["brightness"] = action["dimming"]["brightness"]
                    if "color" in action:
                        xy = action["color"]["xy"]
                        desired["xy"] = [xy["x"], xy["y"]]
                    if "no_effect" in lights[light_id]["supported_effects"]:
                        desired["effect"] = action.get("effects", {}).get("effect", "no_effect")
                    expected[light_id] = desired
                # Stop prior ad-hoc effects; the saved scene can re-enable its own.
                for light_id in rooms[call.arguments["room"]]["lights"]:
                    if "no_effect" in lights[light_id]["supported_effects"]:
                        await client.call_tool(
                            "set_light", {"target": light_id, "state": {"effect": "no_effect"}}
                        )
            else:
                expected[call.arguments["target"]] = call.arguments["state"]
            await client.call_tool(call.tool, call.arguments)
        for _attempt in range(4):
            await asyncio.sleep(1)
            observed = await read("read_lights")
            mismatches = []
            for light_id, desired in expected.items():
                actual = observed[light_id]["state"]
                mismatch = actual["on"] != desired["on"]
                if desired["on"]:
                    if "brightness" in desired:
                        mismatch |= (
                            actual["brightness"] is None
                            or abs(actual["brightness"] - desired["brightness"]) > 1
                        )
                    if "xy" in desired:
                        mismatch |= actual["xy"] is None or any(
                            abs(a - b) > 0.015
                            for a, b in zip(actual["xy"] or [], desired["xy"], strict=True)
                        )
                    if "effect" in desired:
                        mismatch |= actual["effect"] != desired["effect"]
                    if "temperature_kelvin" in desired:
                        mismatch |= (
                            actual["temperature_kelvin"] is None
                            or abs(actual["temperature_kelvin"] - desired["temperature_kelvin"])
                            > 100
                        )
                if mismatch:
                    mismatches.append(observed[light_id]["name"])
            if not mismatches:
                print(json.dumps({"state": state, "verified_lights": len(expected)}))
                return
        raise ValueError(f"Lighting readback did not match: {mismatches}")


if __name__ == "__main__":
    asyncio.run(main())
