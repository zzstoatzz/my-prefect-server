import pytest
from mps.home_lighting import LightChange, expected_states, lighting_calls, plan_changes


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


def test_unreachable_light_does_not_block_remaining_writes(tmp_path, monkeypatch, capsys):
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
            if arguments["target"] == "offline":
                bridge_error = (
                    'Hue bridge: device (light) is "soft off", command (on) may not have effect'
                )
                return SimpleNamespace(is_error=True, content=[SimpleNamespace(text=bridge_error)])
            return SimpleNamespace(is_error=False, content=[])

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
    # the bridge's own words, not just "not confirmed", so the next failing bulb names its cause
    assert "soft off" in capsys.readouterr().err


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


def named_inventory():
    rooms, lights, scenes = inventory()
    for key, light in lights.items():
        light["name"] = key
        light["state"] = {"on": True}
    scenes["right-sahara"]["actions"] = [
        {"target": {"rid": key}, "action": {"on": {"on": True}, "dimming": {"brightness": 40}}}
        for key in rooms["living"]["lights"]
    ]
    return rooms, lights, scenes


def change(**fields):
    return LightChange.model_validate(fields)


def test_all_off_reaches_every_light_and_expects_each_off():
    rooms, lights, scenes = named_inventory()
    calls = plan_changes([change(state={"on": False})], rooms, lights, scenes)
    assert {call.arguments["target"] for call in calls} == set(lights)
    expected = expected_states(calls, lights, scenes, every_light=False)
    assert all(not state.on for state in expected.values())


def test_room_and_light_targets_resolve_by_name_ignoring_case():
    rooms, lights, scenes = named_inventory()
    calls = plan_changes(
        [
            change(target="LIVING ROOM", state={"brightness": 30}),
            change(target="Bedroom", state={"on": False}),
        ],
        rooms,
        lights,
        scenes,
    )
    assert [call.arguments["target"] for call in calls] == ["north", "south", "bedroom"]
    expected = expected_states(calls, lights, scenes, every_light=False)
    assert expected.keys() == {"north", "south", "bedroom"}
    assert expected["north"].on and expected["north"].brightness == 30
    assert not expected["bedroom"].on


def test_scene_change_recalls_the_scene_in_its_own_room():
    rooms, lights, scenes = named_inventory()
    calls = plan_changes([change(target="living room", scene="sahara")], rooms, lights, scenes)
    assert [call.arguments for call in calls] == [
        {"room": "living", "scene": "right-sahara", "action": "active"}
    ]
    expected = expected_states(calls, lights, scenes, every_light=False)
    assert {key: state.brightness for key, state in expected.items()} == {"north": 40, "south": 40}


def test_a_later_change_without_on_keeps_the_earlier_planned_power_state():
    rooms, lights, scenes = named_inventory()
    calls = plan_changes(
        [change(state={"on": False}), change(target="bedroom", state={"brightness": 5})],
        rooms,
        lights,
        scenes,
    )
    assert not expected_states(calls, lights, scenes, every_light=False)["bedroom"].on


def test_group_change_drops_color_for_bulbs_that_cannot_show_it():
    rooms, lights, scenes = named_inventory()
    lights["south"]["capabilities"] = {
        "color": False,
        "temperature_mirek_range": {"mirek_minimum": 153, "mirek_maximum": 400},
    }
    calls = plan_changes(
        [change(target="living room", state={"xy": [0.5, 0.4], "temperature_kelvin": 2200})],
        rooms,
        lights,
        scenes,
    )
    states = {call.arguments["target"]: call.arguments["state"] for call in calls}
    assert states["south"] == {"temperature_kelvin": 2500}
    assert states["north"]["xy"] == (0.5, 0.4)


def test_single_light_change_fails_when_the_bulb_cannot_honor_it():
    rooms, lights, scenes = named_inventory()
    lights["bedroom"]["capabilities"] = {"color": False}
    with pytest.raises(ValueError, match="does not support"):
        plan_changes([change(target="bedroom", state={"xy": [0.5, 0.4]})], rooms, lights, scenes)


@pytest.mark.parametrize(
    "fields",
    [
        {"target": "garage", "state": {"on": False}},
        {"target": "bedroom", "scene": "sahara"},
        {"target": "living room", "scene": "missing"},
    ],
)
def test_unknown_names_fail_before_any_plan_can_be_applied(fields):
    with pytest.raises(ValueError, match="Expected one"):
        plan_changes([change(**fields)], *named_inventory())


@pytest.mark.parametrize(
    "fields",
    [{}, {"state": {}}, {"scene": "x", "state": {"on": True}}, {"state": {"colour": "red"}}],
)
def test_a_change_needs_exactly_one_known_action(fields):
    with pytest.raises(ValueError):
        change(**fields)
