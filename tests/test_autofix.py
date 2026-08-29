from uuid import uuid4

from prefect.testing.utilities import prefect_test_harness

from flows import autofix


class FakeState:
    def __init__(self, name, message):
        self.name, self.message = name, message


class FakeDeployment:
    def __init__(self, name):
        self.name, self.entrypoint = name, "flows/x.py:x"


class FakeRun:
    def __init__(self, name):
        self.id, self.name = uuid4(), name
        self.state = FakeState("Failed", "boom")
        self.parameters = {}
        self.start_time = self.end_time = None


def ctx(dep_name):
    return {
        "run": FakeRun("r"),
        "deployment": FakeDeployment(dep_name),
        "logs": [],
        "failed_tasks": [],
    }


def test_gather_error_never_fails_the_run(monkeypatch):
    async def broken(_):
        raise RuntimeError("api down")

    monkeypatch.setattr(autofix, "gather", broken)
    with prefect_test_harness():
        state = autofix.autofix(uuid4(), return_state=True)
    assert state.is_completed()
    assert state.name == "Degraded"
    assert "api down" in state.message


def test_skips_own_failures(monkeypatch):
    async def fake(_):
        return ctx("autofix")

    monkeypatch.setattr(autofix, "gather", fake)
    with prefect_test_harness():
        state = autofix.autofix(uuid4(), return_state=True)
    assert state.name == "Skipped"


def test_dry_run_renders_without_pi(monkeypatch):
    async def fake(_):
        return ctx("strata")

    monkeypatch.setattr(autofix, "gather", fake)
    monkeypatch.setattr(
        autofix, "run_pi", lambda *a, **k: (_ for _ in ()).throw(AssertionError)
    )
    with prefect_test_harness():
        state = autofix.autofix(uuid4(), dry_run=True, return_state=True)
    assert state.name == "DryRun"
