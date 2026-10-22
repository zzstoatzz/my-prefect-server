"""Regression tests for Fly inventory pricing and attribution."""

import asyncio
import datetime as dt

from mps.costs.connectors import fly
from mps.costs.connectors.fly import (
    FlyConnector,
    _machine_monthly_cents,
    _snapshot_monthly_cents_by_app,
    _uptime_fraction,
    _volume_monthly_cents,
)
from mps.costs.projects import project_for
from mps.costs.types import Period


def test_status_machine_matches_fly_ewr_list_price():
    guest = {"cpu_kind": "shared", "cpus": 1, "memory_mb": 1024}

    assert _machine_monthly_cents(guest, region="ewr") == 592


def test_named_preset_ram_is_not_charged_twice():
    shared = {"cpu_kind": "shared", "cpus": 1, "memory_mb": 256}
    performance = {"cpu_kind": "performance", "cpus": 1, "memory_mb": 2048}

    assert _machine_monthly_cents(shared, region="ewr") == 202
    assert _machine_monthly_cents(performance, region="ewr") == 3219


def test_persistent_volume_cost_is_included():
    assert _volume_monthly_cents([{"size_gb": 1}]) == 15


def test_pending_destroy_volume_is_not_billed():
    assert (
        _volume_monthly_cents(
            [
                {"size_gb": 100, "state": "pending_destroy"},
                {"size_gb": 1, "state": "created"},
            ]
        )
        == 15
    )


def test_status_is_its_own_project():
    assert project_for("zzstoatzz-quickslice-status") == "status"


def test_snapshot_free_tier_is_applied_once_and_attributed_by_app():
    resources = {
        "typeahead-search": {
            "machines": [],
            "volumes": [],
            "snapshots": [{"size": 15_000_000_000}],
        },
        "pds": {
            "machines": [],
            "volumes": [],
            "snapshots": [{"size": 5_000_000_000}],
        },
    }

    # 20 GB stored - 10 GB free = $0.80, allocated 3:1 by stored bytes.
    assert _snapshot_monthly_cents_by_app(resources) == {
        "typeahead-search": 60,
        "pds": 20,
    }


def test_connector_splits_compute_volumes_and_snapshots(monkeypatch):
    monkeypatch.setenv("FLY_API_TOKEN", "token")

    async def fake_resources(_token, _org):
        return {
            "typeahead-search": {
                "machines": [
                    {
                        "state": "started",
                        "region": "ewr",
                        "config": {
                            "guest": {
                                "cpu_kind": "shared",
                                "cpus": 1,
                                "memory_mb": 1024,
                            }
                        },
                    },
                    {
                        "state": "stopped",
                        "region": "ewr",
                        "config": {
                            "guest": {
                                "cpu_kind": "performance",
                                "cpus": 2,
                                "memory_mb": 4096,
                            }
                        },
                    },
                ],
                "volumes": [{"size_gb": 50, "attached_machine_id": "machine-1"}],
                "snapshots": [{"size": 20_000_000_000}],
            }
        }

    async def prometheus_down(_token, _org):
        raise RuntimeError("prometheus unavailable")

    monkeypatch.setattr(fly, "_apps_with_resources", fake_resources)
    monkeypatch.setattr(fly, "_uptime_samples", prometheus_down)
    connector = FlyConnector()
    items = asyncio.run(connector.collect(Period.trailing_month()))

    assert [(item.service, item.amount) for item in items] == [
        ("typeahead-search:compute", 592),
        ("typeahead-search:volumes", 750),
        ("typeahead-search:snapshots", 80),
    ]
    assert "stopped machine rootfs unmeasured" in items[0].usage
    assert all(item.project == "typeahead" for item in items)
    assert any(gap.startswith("machine uptime") for gap in connector.unmeasured)


NOW = dt.datetime(2026, 10, 22, tzinfo=dt.UTC)
WEEK_OF_SAMPLES = 7 * 24 * 12


def _machine(machine_id, state, created="2026-09-01T00:00:00Z"):
    return {
        "id": machine_id,
        "state": state,
        "region": "ewr",
        "created_at": created,
        "config": {"guest": {"cpu_kind": "shared", "cpus": 1, "memory_mb": 1024}},
    }


def test_uptime_fraction_scales_by_reported_samples():
    samples = {"half": WEEK_OF_SAMPLES // 2, "always": WEEK_OF_SAMPLES}

    assert _uptime_fraction(_machine("half", "stopped"), samples, NOW) == 0.5
    assert _uptime_fraction(_machine("always", "started"), samples, NOW) == 1.0


def test_uptime_fraction_measures_young_machines_against_their_age():
    young = _machine("young", "started", created="2026-10-21T00:00:00Z")

    assert _uptime_fraction(young, {"young": 24 * 12}, NOW) == 1.0


def test_uptime_fraction_without_samples_follows_current_state():
    assert _uptime_fraction(_machine("fresh", "started"), {}, NOW) == 1.0
    assert _uptime_fraction(_machine("idle", "stopped"), {}, NOW) == 0.0


def test_on_demand_machines_are_priced_by_uptime(monkeypatch):
    monkeypatch.setenv("FLY_API_TOKEN", "token")

    async def fake_resources(_token, _org):
        return {
            "relay-api-staging": {
                "machines": [
                    _machine("worker", "stopped"),
                    _machine("app", "started"),
                    _machine("standby", "stopped"),
                ],
                "volumes": [],
                "snapshots": [],
            },
            "labelz": {"machines": [], "volumes": [], "snapshots": []},
        }

    async def fake_samples(_token, _org):
        return {"worker": WEEK_OF_SAMPLES // 4, "app": WEEK_OF_SAMPLES // 2}

    monkeypatch.setattr(fly, "_apps_with_resources", fake_resources)
    monkeypatch.setattr(fly, "_uptime_samples", fake_samples)
    connector = FlyConnector()
    items = asyncio.run(connector.collect(Period.trailing_month()))

    assert [(item.service, item.amount) for item in items] == [
        ("relay-api-staging:compute", round(592 * 0.25 + 592 * 0.5)),
    ]
    assert "up 25%" in items[0].usage and "up 50%" in items[0].usage
    assert not any(gap.startswith("machine uptime") for gap in connector.unmeasured)
