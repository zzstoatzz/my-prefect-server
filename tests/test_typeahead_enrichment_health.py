import httpx
import pytest

from flows.typeahead_enrich_backfill import _ingestion_ready


@pytest.mark.parametrize(
    "change",
    [
        {},
        {"queued": 400},
        {"deletes_queued": 400},
        {"backpressure_active": True},
        {"source_event_at": 880},
        {"last_write_at": 940},
        {"source_event_at": 1001},
        {"source_event_at": None},
    ],
)
def test_bulk_writes_require_recent_uncongested_ingestion(change):
    ingest = {
        "queued": 0,
        "deletes_queued": 0,
        "backpressure_active": False,
        "source_event_at": 999,
        "last_write_at": 999,
    }
    ingest.update(change)
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"status": "ok", "ingest": ingest})
        )
    ) as client:
        assert _ingestion_ready(client, 1000) is (not change)


@pytest.mark.parametrize("status,body", [(503, {}), (200, {}), (200, "invalid")])
def test_unavailable_or_old_health_response_yields(status, body):
    with httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(status, json=body))
    ) as client:
        assert not _ingestion_ready(client, 1000)


def test_waiting_exhausts_budget_without_flushing_pending_writes(monkeypatch):
    import importlib
    from unittest.mock import Mock

    module = importlib.import_module("flows.typeahead_enrich_backfill")
    clock = [0.0]
    writes = []

    def query(client, statements):
        if statements[0]["sql"].startswith("SELECT"):
            return [{"rows": [[{"value": "1"}, {"value": "did:plc:example"}, {"value": "1"}]]}]
        writes.extend(statements)
        return []

    client = httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"profiles": []}))
    )
    monkeypatch.setattr(module.httpx, "Client", lambda **kwargs: client)
    monkeypatch.setattr(module, "get_run_logger", Mock)
    monkeypatch.setattr(module, "_tq", query)
    monkeypatch.setattr(module, "_ingestion_ready", lambda *args: False)
    monkeypatch.setattr(module.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(
        module.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds)
    )
    try:
        result = module.typeahead_enrich_backfill.fn(
            limit=1, budget_seconds=10, write_batch=1, dry_run=False
        )
    finally:
        client.close()
    assert result["budget_spent"] is True
    assert clock[0] == 10
    assert writes == []
