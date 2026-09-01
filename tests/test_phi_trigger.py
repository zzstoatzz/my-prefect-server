from prefect.testing.utilities import prefect_test_harness

from flows import phi_trigger


class FakeResponse:
    def raise_for_status(self):
        pass

    def json(self):
        return {"triggered": "x"}


def _capture(monkeypatch):
    calls = []

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def post(self, url, headers, json=None):
            calls.append({"url": url, "json": json})
            return FakeResponse()

    monkeypatch.setattr(phi_trigger.httpx, "AsyncClient", FakeClient)
    monkeypatch.setattr(phi_trigger, "configure_logfire", lambda *a, **k: None)

    class S:
        @staticmethod
        async def load(name):
            return type("B", (), {"get": lambda self: "tok"})()

    monkeypatch.setattr(phi_trigger, "Secret", S)
    return calls


async def test_clock_slot_sends_no_body(monkeypatch):
    calls = _capture(monkeypatch)
    with prefect_test_harness():
        await phi_trigger.phi_trigger("curation")
    assert calls == [
        {"url": f"{phi_trigger.PHI_BASE}/api/control/trigger/curation", "json": None}
    ]


async def test_material_rides_as_the_json_body(monkeypatch):
    calls = _capture(monkeypatch)
    with prefect_test_harness():
        await phi_trigger.phi_trigger(
            "pull-review", material="at://x/sh.tangled.repo.pull/1"
        )
    assert calls[0]["url"].endswith("/trigger/pull-review")
    assert calls[0]["json"] == {"material": "at://x/sh.tangled.repo.pull/1"}
