import importlib
import io
import json
from pathlib import Path
from urllib.parse import urlsplit

import pytest

SOURCE = Path(__file__).resolve().parents[1] / "deploy/prefect-edge/typeahead_health.py"


@pytest.fixture
def health(monkeypatch):
    monkeypatch.syspath_prepend(str(SOURCE.parent))
    return importlib.import_module("typeahead_health")


class Response(io.BytesIO):
    status = 200


def serve(monkeypatch, health, payload):
    monkeypatch.setattr(
        health.urllib.request,
        "urlopen",
        lambda request, timeout: Response(json.dumps(payload).encode()),
    )


def test_tantivy_index_status_becomes_index_series(monkeypatch, health):
    serve(monkeypatch, health, {"ready": True, "actor_count": 13154389, "sync_lag_s": 5})
    lines = health.probe("index", health.SOURCES["index"]).splitlines()
    assert 'typeahead_endpoint_up{source="index"} 1' in lines
    assert "typeahead_snapshot_actors 13154389.0" in lines
    assert "typeahead_index_sync_lag_seconds 5.0" in lines


def test_transport_readiness_becomes_search_ready(monkeypatch, health):
    serve(monkeypatch, health, {"ready": True})
    assert "typeahead_search_ready 1.0" in health.probe("search", health.SOURCES["search"])


def test_no_probe_targets_the_retired_native_search_app(health):
    # typeahead-search was deleted with the native engine; its probes read 0 for days.
    hosts = {urlsplit(url).hostname for url in health.SOURCES.values()}
    assert "typeahead-search.fly.dev" not in hosts
