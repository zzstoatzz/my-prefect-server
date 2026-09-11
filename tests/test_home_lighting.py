import pytest
from mps.home_lighting import lighting_calls


def inventory():
    rooms = {"living": {"name": "Living room", "lights": ["north", "south"]}}
    lights = {
        key: {"supported_effects": ["candle", "no_effect"], "capabilities": {"color": True}}
        for key in ("north", "south", "bedroom", "unassigned")
    }
    scenes = {
        "wrong-sahara": {"name": "Sahara", "room_id": "bedroom"},
        "right-sahara": {"name": "Sahara", "room_id": "living"},
    }
    return rooms, lights, scenes


def test_arrival_scopes_sahara_and_ambers_every_other_light():
    calls = lighting_calls("home", *inventory())
    assert calls[0].arguments == {"room": "living", "scene": "right-sahara", "action": "active"}
    assert [call.arguments["target"] for call in calls[1:]] == ["bedroom", "unassigned"]
    for call in calls[1:]:
        assert call.arguments["state"] == {
            "on": True,
            "brightness": 15,
            "transition_seconds": 1,
            "effect": "no_effect",
            "xy": [0.526, 0.413],
        }


def test_departure_turns_off_unassigned_lights_too_without_needing_scenes():
    _, lights, _ = inventory()
    calls = lighting_calls("away", {}, lights, {})
    assert {call.arguments["target"] for call in calls} == set(lights)
    assert all(call.arguments["state"] == {"on": False} for call in calls)


def test_missing_sahara_fails_before_any_plan_can_be_applied():
    rooms, lights, _ = inventory()
    with pytest.raises(ValueError, match="Sahara"):
        lighting_calls("home", rooms, lights, {})


def test_temperature_only_bulb_uses_supported_warm_limit():
    rooms, lights, scenes = inventory()
    lights["bedroom"]["capabilities"] = {
        "color": False,
        "temperature_mirek_range": {"mirek_minimum": 153, "mirek_maximum": 400},
    }
    call = next(
        c
        for c in lighting_calls("home", rooms, lights, scenes)
        if c.arguments.get("target") == "bedroom"
    )
    assert call.arguments["state"]["temperature_kelvin"] == 2500
    assert "xy" not in call.arguments["state"]


@pytest.mark.parametrize("succeeds", [True, False])
def test_lighting_marker_advances_only_after_verified_success(tmp_path, monkeypatch, succeeds):
    from datetime import UTC, datetime

    from mps.presence import Presence, PresenceRecord

    from flows.presence_lighting import apply_presence_lighting

    marker = tmp_path / "lighting-state"
    marker.write_text("away\n")
    monkeypatch.setenv("PRESENCE_LIGHTING_STATE_FILE", str(marker))
    calls = []

    async def run(state):
        calls.append(state)
        if not succeeds:
            raise ValueError("Lighting readback failed")
        return 13

    monkeypatch.setattr("mps.lighting.apply_lighting", run)
    record = PresenceRecord(
        uri="test", cid="test", value=Presence(state="home", observedAt=datetime.now(UTC))
    )
    if succeeds:
        assert apply_presence_lighting.fn(record) == "ARRIVAL_LOOK"
        assert marker.read_text().strip() == "home"
        assert apply_presence_lighting.fn(record) == "NO_CHANGE"
        assert len(calls) == 1
    else:
        with pytest.raises(ValueError, match="Lighting readback failed"):
            apply_presence_lighting.fn(record)
        assert marker.read_text().strip() == "away"


def test_stale_report_cannot_actuate_newer_presence(monkeypatch):
    from contextlib import nullcontext
    from datetime import UTC, datetime, timedelta

    from mps.presence import Presence, PresenceRecord, PresenceUpdate

    from flows.presence_lighting import report_presence

    now = datetime.now(UTC)
    current = PresenceRecord(uri="test", cid="test", value=Presence(state="away", observedAt=now))
    monkeypatch.setattr("flows.presence_lighting.concurrency", lambda *a, **k: nullcontext())
    monkeypatch.setattr("flows.presence_lighting.store_presence", lambda report: current)

    def unexpected(record):
        pytest.fail("Stale report attempted lighting")

    monkeypatch.setattr("flows.presence_lighting.apply_presence_lighting", unexpected)
    assert (
        report_presence.fn(
            PresenceUpdate(state="home", observedAt=now - timedelta(minutes=1)), apply_lighting=True
        )
        == "NO_CHANGE"
    )


def test_unreachable_light_does_not_block_remaining_writes(tmp_path, monkeypatch):
    import asyncio
    import sys
    from types import ModuleType, SimpleNamespace

    lights = {
        "offline": {
            "name": "offline",
            "connectivity": "connectivity_issue",
            "state": {"on": False},
        },
        "online": {"name": "online", "connectivity": "connected", "state": {"on": False}},
    }
    writes = []

    class Client:
        def __init__(self, server):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def call_tool(self, tool, arguments, **kwargs):
            if tool.startswith("read_"):
                return SimpleNamespace(structured_content=lights if tool == "read_lights" else {})
            writes.append(arguments["target"])
            return SimpleNamespace(is_error=arguments["target"] == "offline")

    client_module = ModuleType("fastmcp")
    client_module.Client = Client
    server_module = ModuleType("smart_home.lights.server")
    server_module.lights_mcp = object()
    monkeypatch.setitem(sys.modules, "fastmcp", client_module)
    monkeypatch.setitem(sys.modules, "smart_home.lights.server", server_module)
    config = tmp_path / "hue.json"
    config.write_text("{}")
    monkeypatch.setenv("PRESENCE_HUE_ENV_FILE", str(config))

    async def sleep(seconds):
        pass

    monkeypatch.setattr(asyncio, "sleep", sleep)
    from mps.lighting import apply_lighting

    with pytest.raises(ValueError, match="offline"):
        asyncio.run(apply_lighting("away"))
    assert writes == ["offline", "online"]


def test_incomplete_saved_scene_is_rejected_before_execution():
    from mps.home_lighting import expected_states

    rooms, lights, scenes = inventory()
    scenes["right-sahara"]["actions"] = [
        {"target": {"rid": "north"}, "action": {"on": {"on": True}}}
    ]
    with pytest.raises(ValueError, match="every light"):
        expected_states(lighting_calls("home", rooms, lights, scenes), lights, scenes)


def test_readback_checks_color_brightness_effect_and_connectivity():
    from mps.home_lighting import LightState, readback_mismatches

    expected = {"lamp": LightState(on=True, brightness=15, xy=(0.526, 0.413), effect="no_effect")}
    observed = {
        "lamp": {
            "name": "lamp",
            "connectivity": "connected",
            "state": {"on": True, "brightness": 15.02, "xy": [0.526, 0.413], "effect": "no_effect"},
        }
    }
    assert readback_mismatches(expected, observed) == []
    for field, value in (("brightness", 80), ("xy", [0.2, 0.2]), ("effect", "candle")):
        changed = {
            "lamp": {**observed["lamp"], "state": {**observed["lamp"]["state"], field: value}}
        }
        assert readback_mismatches(expected, changed) == ["lamp"]
    observed["lamp"]["connectivity"] = "connectivity_issue"
    assert readback_mismatches(expected, observed) == ["lamp"]
