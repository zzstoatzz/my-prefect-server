import datetime as dt
from decimal import Decimal

from mps.builder_billing import amount_usd, merge_records, record


def test_billing_rounds_each_vm_up_to_hour_and_caps_each_month(tmp_path):
    server = {
        "id": 12,
        "name": "prefect-build-example",
        "created": "2026-10-27T00:00:00Z",
        "labels": {"flow-run": "run"},
        "location": {"name": "fsn1"},
        "server_type": {"id": 1, "name": "cpx32"},
    }
    rate = {
        "location": "fsn1",
        "price_hourly": {"gross": "0.01"},
        "price_monthly": {"gross": "5.00"},
    }
    pricing = {
        "currency": "USD",
        "server_types": [{"id": 1, "prices": [rate]}],
        "primary_ips": [{"type": "ipv4", "prices": [rate]}],
    }
    path = record(tmp_path, server, pricing, "created")
    import json

    item = json.loads(path.read_text())
    assert amount_usd(item, dt.datetime(2026, 10, 27, 0, 1, tzinfo=dt.UTC)) == Decimal("0.02")
    assert record(tmp_path, server, pricing, "created").read_text() == path.read_text()
    assert amount_usd(item, dt.datetime(2026, 12, 1, tzinfo=dt.UTC)) == Decimal("12.40")


def test_receipt_merge_deduplicates_worker_and_reaper_and_prefers_first_deletion():
    created = {"id": 12, "phase": "created", "observed_at": "2026-10-27T00:00:00Z"}
    deleted = {**created, "phase": "deleted", "observed_at": "2026-10-27T00:05:00Z"}
    later = {**deleted, "observed_at": "2026-10-27T00:06:00Z"}
    assert merge_records([created, later, deleted, created]) == [deleted]
