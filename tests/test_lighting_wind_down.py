from contextlib import asynccontextmanager, nullcontext
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from mps import lighting as hue
from mps.wind_down import WindDown

from flows import lighting
from scripts import lighting_override


async def test_fade_sends_once_only_to_lights_it_can_dim(monkeypatch):
    lights = {
        key: {
            "name": key,
            "connectivity": connectivity,
            "state": {"on": on, "brightness": level},
            "capabilities": {"minimum_brightness": minimum},
        }
        for key, level, on, connectivity, minimum in [
            ("lamp", 40, True, "connected", 8),
            ("off", 40, False, "connected", 0.2),
            ("already dim", 3, True, "connected", 0.2),
            ("unknown brightness", None, True, "connected", 0.2),
            ("offline", 40, True, "connectivity_issue", 0.2),
            ("unconfirmed", 50, True, "connected", 0.2),
            ("next lamp", 60, True, "connected", 0.2),
        ]
    }
    writes = []

    class Client:
        async def call_tool(self, tool, arguments, **kwargs):
            if tool == "read_lights":
                return SimpleNamespace(structured_content=lights)
            assert tool == "set_light"
            writes.append(arguments)
            return SimpleNamespace(is_error=arguments["target"] == "unconfirmed", content=[])

    @asynccontextmanager
    async def client():
        yield Client()

    monkeypatch.setattr(hue, "hue_client", client)
    result = await hue.apply_wind_down(datetime.now(UTC) + timedelta(minutes=20), 5)
    assert result.accepted == ["lamp", "next lamp"]
    assert result.unreachable == ["offline"]
    assert result.unconfirmed == ["unconfirmed"]
    assert [write["target"] for write in writes] == ["lamp", "unconfirmed", "next lamp"]
    for write, brightness in zip(writes, (8, 5, 5), strict=True):
        state = write["state"]
        assert state.keys() == {"brightness", "transition_seconds"}
        assert state["brightness"] == brightness
        assert 1190 < state["transition_seconds"] <= 1200


@pytest.mark.parametrize(
    ("is_override", "stored_run", "delay_minutes", "expected"),
    [
        (False, None, 1, "applied"),
        (False, "this-run", 1, "SUPERSEDED"),
        (True, None, 1, "SUPERSEDED"),
        (True, "older-run", 1, "SUPERSEDED"),
        (True, "this-run", 1, "applied"),
        (True, "this-run", 6, "STALE"),
        (False, None, -1, "STALE"),
    ],
)
def test_fade_runs_only_for_the_current_night_owner(
    monkeypatch, is_override, stored_run, delay_minutes, expected
):
    start = datetime(2026, 11, 25, 8, tzinfo=UTC)
    now = start + timedelta(minutes=delay_minutes)
    variables = []

    def get_variable(name):
        variables.append(name)
        return stored_run

    async def apply(end, brightness):
        assert end == start + timedelta(minutes=20)
        assert brightness == 5
        return hue.WindDownResult(accepted=["lamp"])

    monkeypatch.setattr(lighting, "datetime", SimpleNamespace(now=lambda tz: now))
    monkeypatch.setattr(
        lighting, "flow_run", SimpleNamespace(scheduled_start_time=start, id="this-run")
    )
    monkeypatch.setattr(lighting, "concurrency", lambda *args, **kwargs: nullcontext())
    monkeypatch.setattr(lighting.Variable, "get", get_variable)
    monkeypatch.setattr(lighting, "apply_wind_down", apply)
    result = lighting.lighting.fn(wind_down=WindDown(override=is_override))
    assert variables == ["lighting-wind-down-2026-11-25"]
    assert result == (hue.WindDownResult(accepted=["lamp"]) if expected == "applied" else expected)


def test_override_enqueues_before_replacing_default_and_uses_local_date(monkeypatch):
    start = lighting_override.parse_start("2026-11-25T02:00")
    assert start == datetime(2026, 11, 25, 8, tzinfo=UTC)
    calls = []

    def create_run(deployment_id, **options):
        assert deployment_id == "lighting"
        assert options["state"].state_details.scheduled_time == start
        assert options["idempotency_key"] == "lighting-wind-down:2026-11-25T08:00:00+00:00:25"
        calls.append(options["parameters"])
        return SimpleNamespace(id="override-run")

    client = SimpleNamespace(
        read_deployment_by_name=lambda name: SimpleNamespace(
            id="lighting", paused=False, parameter_openapi_schema={"properties": {"wind_down": {}}}
        ),
        create_flow_run_from_deployment=create_run,
    )
    monkeypatch.setattr(
        lighting_override, "datetime", SimpleNamespace(now=lambda tz: start - timedelta(hours=2))
    )
    monkeypatch.setattr(lighting_override, "get_client", lambda **options: nullcontext(client))
    monkeypatch.setattr(
        lighting_override.Variable,
        "set",
        lambda name, value, **options: calls.append((name, value, options["overwrite"])),
    )
    lighting_override.schedule_override(start, 25)
    assert calls == [
        {"wind_down": {"minutes": 25, "brightness": 5, "override": True}},
        ("lighting-wind-down-2026-11-25", "override-run", True),
    ]


@pytest.mark.parametrize("value", ["2026-11-01T01:30", "2026-03-08T02:30"])
def test_override_requires_an_offset_during_clock_changes(value):
    with pytest.raises(ValueError, match="UTC offset"):
        lighting_override.parse_start(value)
