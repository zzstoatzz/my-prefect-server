"""Regression tests for Fly inventory pricing."""

from mps.costs.connectors.fly import _machine_monthly_cents, _volume_monthly_cents
from mps.costs.projects import project_for


def test_status_machine_matches_fly_ewr_list_price():
    guest = {"cpu_kind": "shared", "cpus": 1, "memory_mb": 1024}

    assert _machine_monthly_cents(guest, started=True, region="ewr") == 570


def test_named_preset_ram_is_not_charged_twice():
    shared = {"cpu_kind": "shared", "cpus": 1, "memory_mb": 256}
    performance = {"cpu_kind": "performance", "cpus": 1, "memory_mb": 2048}

    assert _machine_monthly_cents(shared, started=True, region="ewr") == 194
    assert _machine_monthly_cents(performance, started=True, region="ewr") == 3100


def test_persistent_volume_cost_is_included():
    assert _volume_monthly_cents([{"size_gb": 1}]) == 15


def test_status_is_its_own_project():
    assert project_for("zzstoatzz-quickslice-status") == "status"
