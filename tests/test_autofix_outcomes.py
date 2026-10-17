import time

from prefect import flow, get_client
from prefect.events.filters import EventFilter, EventNameFilter
from prefect.events.worker import EventsWorker
from prefect.states import Failed
from prefect.testing.utilities import prefect_test_harness

from flows.autofix import autofix


def test_error_is_persisted_and_next_attempt_reuses_it(monkeypatch):
    monkeypatch.delenv("PREFECT_API_URL", raising=False)

    @flow
    def source():
        pass

    with prefect_test_harness():
        with get_client(sync_client=True) as client:
            failed = client.create_flow_run(source, state=Failed(message="provider unavailable"))
        first = autofix(failed.id, return_state=True)
        assert first.is_completed() and first.name == "Degraded"
        assert "anthropic-api-key" in first.message
        EventsWorker.drain_all(timeout=10)
        deadline = time.monotonic() + 20
        with get_client(sync_client=True) as client:
            while True:
                events = client.read_events(
                    EventFilter(event=EventNameFilter(name=["autofix.error"])), limit=50
                ).events
                if events or time.monotonic() >= deadline:
                    break
                time.sleep(0.2)
        assert len(events) == 1
        event = events[0]
        assert event.payload["stage"] == "screen"
        assert event.payload["incident_key"]
        assert any(
            r.id == f"prefect.flow-run.{failed.id}" and r.role == "about" for r in event.related
        )
        second = autofix(failed.id, return_state=True)
        assert second.name == "Duplicate"
        assert event.payload["autofix_url"] in second.message
        forced = autofix(failed.id, force=True, return_state=True)
        assert forced.name == "Degraded"
