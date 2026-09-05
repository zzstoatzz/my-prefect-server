"""The agreed arrival/departure preset, expressed as smart-home MCP calls."""

from typing import Any, Literal

from pydantic import BaseModel


class LightingCall(BaseModel):
    tool: Literal["set_light", "activate_scene"]
    arguments: dict[str, Any]


def lighting_calls(
    state: Literal["home", "away"],
    rooms: dict[str, Any],
    lights: dict[str, Any],
    scenes: dict[str, Any],
) -> list[LightingCall]:
    """Resolve the entire preset before making writes; never guess a scene or room."""
    if not lights:
        raise ValueError("The Hue bridge returned no lights")
    if state == "away":
        return [
            LightingCall(tool="set_light", arguments={"target": key, "state": {"on": False}})
            for key in lights
        ]
    living = [key for key, room in rooms.items() if room["name"].casefold() == "living room"]
    if len(living) != 1:
        raise ValueError("Expected one room named Living room")
    room_id = living[0]
    sahara = [
        key
        for key, scene in scenes.items()
        if scene["room_id"] == room_id and scene["name"].casefold() == "sahara"
    ]
    if len(sahara) != 1:
        raise ValueError("Expected one Sahara scene in the living room")
    members = set(rooms[room_id]["lights"])
    if not members or not members <= lights.keys():
        raise ValueError("Living-room membership does not match the light inventory")
    calls = [
        LightingCall(
            tool="activate_scene",
            arguments={"room": room_id, "scene": sahara[0], "action": "active"},
        )
    ]
    for key, light in lights.items():
        if key in members:
            continue
        update: dict[str, Any] = {"on": True, "brightness": 15, "transition_seconds": 1}
        if "no_effect" in light["supported_effects"]:
            update["effect"] = "no_effect"
        capabilities = light["capabilities"]
        if capabilities["color"]:
            update["xy"] = [0.526, 0.413]
        elif capabilities.get("temperature_mirek_range"):
            bounds = capabilities["temperature_mirek_range"]
            mirek = min(
                max(round(1_000_000 / 2200), bounds["mirek_minimum"]), bounds["mirek_maximum"]
            )
            update["temperature_kelvin"] = round(1_000_000 / mirek)
        calls.append(LightingCall(tool="set_light", arguments={"target": key, "state": update}))
    return calls
