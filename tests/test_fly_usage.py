import datetime as dt
import json
from copy import deepcopy

import pytest
from mps.costs.connectors.fly import _machine_monthly_cents, _snapshot_compute, _uptime_compute
from mps.costs.fly_usage import is_ad_hoc, line_items, merge_day, usage_rows

NOW = dt.datetime(2026, 11, 5, 8, tzinfo=dt.UTC)


def observation(day="2026-11-05", seconds=13 * 60):
    return {
        "day": day,
        "observed_to": f"{day}T08:00:00+00:00",
        "rows": [
            {
                "app": "zkv-live-35ffc881b5",
                "instance": "d895101b777178",
                "region": "ord",
                "cpu_kind": "performance",
                "cpu_count": "2",
                "memory_mb": "4096",
                "seconds": seconds,
                "ad_hoc": True,
            }
        ],
    }


def test_temporary_benchmark_is_usage_not_a_monthly_projection():
    machine = {
        "id": "d895101b777178",
        "state": "started",
        "created_at": "2026-11-05T07:49:09Z",
        "region": "ord",
        "config": {
            "auto_destroy": True,
            "guest": {
                "cpu_kind": "performance",
                "cpus": 2,
                "memory_mb": 4096,
            },
        },
    }
    recurring = [machine for machine in [machine] if not is_ad_hoc(machine)]
    assert _uptime_compute(recurring, {machine["id"]: 3}, NOW) is None
    assert _snapshot_compute(recurring, 0) is None
    items, _ = line_items([observation()], NOW, _machine_monthly_cents)
    assert [(item.amount, item.basis) for item in items] == [(2, "month_to_date")]
    assert "ad-hoc-compute" in items[0].service


def test_regular_and_explicit_usage_machines_do_not_depend_on_names():
    assert not is_ad_hoc({"config": {"auto_destroy": False}})
    assert not is_ad_hoc({"config": {"restart": {"policy": "no"}}})
    assert is_ad_hoc({"config": {"metadata": {"mps.cost_basis": "month_to_date"}}})


def test_deleted_machine_usage_survives_roundtrip_retries_and_metrics_expiry(tmp_path):
    day = observation("2026-11-01")
    day["observed_to"] = "2026-11-02T00:00:00+00:00"
    path = tmp_path / "receipt.json"
    path.write_text(json.dumps(day))
    retained = json.loads(path.read_text())
    assert merge_day(retained, retained) == retained
    expired = {**day, "rows": []}
    merged = merge_day(retained, expired)
    assert merged == retained
    later = dt.datetime(2026, 11, 25, tzinfo=dt.UTC)
    items, gaps = line_items([merged], later, _machine_monthly_cents)
    assert items[0].amount == 2
    assert gaps
    december, _ = line_items(
        [merged], dt.datetime(2026, 12, 1, tzinfo=dt.UTC), _machine_monthly_cents
    )
    assert december == []


def test_accumulation_prices_each_size_and_rounds_only_after_aggregation():
    first = observation("2026-11-01", seconds=300)
    second = observation("2026-11-02", seconds=300)
    later = deepcopy(second)
    later["rows"][0]["seconds"] = 600
    stale = deepcopy(second)
    stale["rows"][0]["seconds"] = 60
    merged = merge_day(merge_day(second, later), stale)
    items, _ = line_items([first, merged], NOW, _machine_monthly_cents)
    assert items[0].amount == round(8049 * 900 / (30 * 86400))
    resized = deepcopy(merged["rows"][0])
    resized.update(memory_mb="8192", seconds=300)
    merged["rows"].append(resized)
    items, _ = line_items([first, merged], NOW, _machine_monthly_cents)
    assert items[0].amount == round((8049 * 900 + 10646 * 300) / (30 * 86400))


def test_recurring_usage_is_excluded_and_partial_days_are_reported():
    day = observation()
    day["rows"][0]["ad_hoc"] = False
    items, gaps = line_items([day], NOW, _machine_monthly_cents)
    assert items == []
    assert "2026-11-01" in gaps[0]
    assert "2026-11-05" not in gaps[0]
    items, gaps = line_items([observation()], NOW + dt.timedelta(hours=1), _machine_monthly_cents)
    assert "2026-11-05" in gaps[0]
    assert "Coverage incomplete" in items[0].note


def test_metric_rows_retain_machine_sizes_and_reject_partial_or_invalid_data():
    row = observation()["rows"][0]
    result = {
        "status": "success",
        "data": {
            "resultType": "vector",
            "result": [
                {
                    "metric": {
                        key: value for key, value in row.items() if key not in {"seconds", "ad_hoc"}
                    },
                    "value": [NOW.timestamp(), "13"],
                }
            ],
        },
    }
    assert usage_rows(result, 86400)[0]["seconds"] == 780
    assert usage_rows(result, 120)[0]["seconds"] == 120
    with pytest.raises(RuntimeError):
        usage_rows({**result, "isPartial": True}, 86400)
    for invalid in ["NaN", "-1"]:
        malformed = deepcopy(result)
        malformed["data"]["result"][0]["value"][1] = invalid
        with pytest.raises(ValueError):
            usage_rows(malformed, 86400)
