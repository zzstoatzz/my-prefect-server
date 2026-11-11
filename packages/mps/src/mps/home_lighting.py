"""Lighting plans, expressed as smart-home MCP calls: the presence preset and arbitrary changes."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


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


class LightUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    on: bool | None = None
    brightness: float | None = Field(default=None, ge=0, le=100)
    temperature_kelvin: int | None = Field(default=None, gt=0)
    xy: tuple[float, float] | None = None
    effect: str | None = None
    effect_speed: float | None = Field(default=None, ge=0, le=1)
    transition_seconds: float | None = Field(default=None, ge=0)


class LightChange(BaseModel):
    """One change to the lights: a saved scene in a room, or a state for a set of lights."""

    model_config = ConfigDict(extra="forbid")

    target: str = Field(default="all", description='"all", a room name, or a light name')
    scene: str | None = Field(default=None, description="Saved scene in the target room")
    state: LightUpdate | None = None

    @model_validator(mode="after")
    def one_action(self) -> "LightChange":
        if (self.scene is None) == (self.state is None):
            raise ValueError("Give exactly one of scene or state")
        if self.state is not None and not self.state.model_fields_set:
            raise ValueError("state is empty")
        return self


def plan_changes(
    changes: list[LightChange],
    rooms: dict[str, Any],
    lights: dict[str, Any],
    scenes: dict[str, Any],
) -> list[LightingCall]:
    """Resolve every change before making writes; never guess a room, light or scene."""
    if not changes:
        raise ValueError("No lighting changes given")
    if not lights:
        raise ValueError("The Hue bridge returned no lights")
    calls = []
    for change in changes:
        name = change.target.casefold()
        room_ids = [key for key, room in rooms.items() if room["name"].casefold() == name]
        if change.scene is not None:
            if len(room_ids) != 1:
                raise ValueError(f"Expected one room named {change.target}")
            scene_ids = [
                key
                for key, scene in scenes.items()
                if scene["room_id"] == room_ids[0]
                and scene["name"].casefold() == change.scene.casefold()
            ]
            if len(scene_ids) != 1:
                raise ValueError(f"Expected one {change.scene} scene in {change.target}")
            calls.append(
                LightingCall(
                    tool="activate_scene",
                    arguments={"room": room_ids[0], "scene": scene_ids[0], "action": "active"},
                )
            )
            continue
        assert change.state is not None
        update = change.state.model_dump(exclude_none=True)
        light_ids = [key for key, light in lights.items() if light["name"].casefold() == name]
        if name == "all":
            members, one_light = list(lights), False
        elif len(room_ids) + len(light_ids) != 1:
            raise ValueError(f"Expected one room or light named {change.target}")
        elif room_ids:
            members, one_light = rooms[room_ids[0]]["lights"], False
            if not members or not set(members) <= lights.keys():
                raise ValueError(f"{change.target} membership does not match the light inventory")
        else:
            members, one_light = light_ids, True
        for key in members:
            supported = supported_update(update, lights[key])
            if one_light and supported.keys() != update.keys():
                raise ValueError(
                    f"{change.target} does not support {sorted(update.keys() - supported.keys())}"
                )
            if supported:
                calls.append(
                    LightingCall(tool="set_light", arguments={"target": key, "state": supported})
                )
    return calls


def supported_update(update: dict[str, Any], light: dict[str, Any]) -> dict[str, Any]:
    """A group change reaches bulbs of mixed capability; each gets the fields it can honor."""
    supported = dict(update)
    capabilities = light["capabilities"]
    if not capabilities.get("color"):
        supported.pop("xy", None)
    bounds = capabilities.get("temperature_mirek_range")
    if "temperature_kelvin" in supported:
        if bounds:
            mirek = min(
                max(round(1_000_000 / supported["temperature_kelvin"]), bounds["mirek_minimum"]),
                bounds["mirek_maximum"],
            )
            supported["temperature_kelvin"] = round(1_000_000 / mirek)
        else:
            del supported["temperature_kelvin"]
    if supported.get("effect") not in (None, *light["supported_effects"]):
        del supported["effect"]
    if "effect" not in supported:
        supported.pop("effect_speed", None)
    if supported.keys() <= {"transition_seconds"}:
        return {}
    return supported


class LightState(BaseModel):
    """The subset of Hue state that this policy promises to verify."""

    on: bool
    brightness: float | None = None
    xy: tuple[float, float] | None = None
    effect: str | None = None
    temperature_kelvin: float | None = None


def expected_states(
    calls: list[LightingCall],
    lights: dict[str, Any],
    scenes: dict[str, Any],
    *,
    every_light: bool = True,
) -> dict[str, LightState]:
    """Validate the complete readback contract before sending any commands."""
    expected: dict[str, LightState] = {}
    for call in calls:
        if call.tool == "set_light":
            light_id, state = call.arguments["target"], call.arguments["state"]
            if "on" not in state:
                earlier = expected.get(light_id)
                on = earlier.on if earlier else lights[light_id]["state"]["on"]
                state = {"on": on, **state}
            expected[light_id] = LightState.model_validate(state)
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
    if every_light and expected.keys() != lights.keys():
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
