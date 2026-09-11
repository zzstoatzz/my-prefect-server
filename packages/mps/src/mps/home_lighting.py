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


class LightState(BaseModel):
    """The subset of Hue state that this policy promises to verify."""

    on: bool
    brightness: float | None = None
    xy: tuple[float, float] | None = None
    effect: str | None = None
    temperature_kelvin: float | None = None


def expected_states(
    calls: list[LightingCall], lights: dict[str, Any], scenes: dict[str, Any]
) -> dict[str, LightState]:
    """Validate the complete readback contract before sending any commands."""
    expected: dict[str, LightState] = {}
    for call in calls:
        if call.tool == "set_light":
            expected[call.arguments["target"]] = LightState.model_validate(call.arguments["state"])
            continue
        for entry in scenes[call.arguments["scene"]]["actions"]:
            light_id = entry["target"]["rid"]
            if light_id not in lights:
                raise ValueError("Scene references a light absent from the inventory")
            action = entry["action"]
            xy = action.get("color", {}).get("xy")
            expected[light_id] = LightState(
                on=action.get("on", {}).get("on", True),
                brightness=action.get("dimming", {}).get("brightness"),
                xy=(xy["x"], xy["y"]) if xy else None,
                effect=action.get("effects", {}).get("effect", "no_effect")
                if "no_effect" in lights[light_id]["supported_effects"]
                else None,
            )
    if expected.keys() != lights.keys():
        raise ValueError("Preset does not cover every light; inspect the saved scene")
    return expected


def readback_mismatches(expected: dict[str, LightState], observed: dict[str, Any]) -> list[str]:
    mismatches = []
    for light_id, desired in expected.items():
        light = observed.get(light_id)
        if light is None:
            mismatches.append(light_id)
            continue
        actual = LightState.model_validate(light["state"])
        matches = light["connectivity"] == "connected" and actual.on == desired.on
        if desired.on:
            for field, tolerance in (("brightness", 1), ("temperature_kelvin", 100)):
                want = getattr(desired, field)
                got = getattr(actual, field)
                if want is not None:
                    matches &= got is not None and abs(got - want) <= tolerance
            if desired.xy is not None:
                matches &= actual.xy is not None and all(
                    abs(a - b) <= 0.015 for a, b in zip(actual.xy, desired.xy, strict=True)
                )
            if desired.effect is not None:
                matches &= actual.effect == desired.effect
        if not matches:
            mismatches.append(light["name"])
    return mismatches
