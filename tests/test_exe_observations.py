import asyncio
import json
import sys
from pathlib import Path
from uuid import uuid4

import pytest
from prefect_exe import ExeJobConfiguration, ExeWorker, bootstrap, runtime
from prefect_exe.observations import Attempt, AttemptStore


async def test_failed_cleanup_survives_worker_restart_and_success_clears_attention(tmp_path):
    path = tmp_path / "observations.sqlite"
    worker = ExeWorker(work_pool_name="test", observations_path=path)
    worker._record("vm-one", flow_run_id=str(uuid4()))

    async def failed_delete():
        raise ConnectionError("credential sentinel must not enter observations")

    with pytest.raises(ConnectionError):
        await worker._measure_stage("vm-one", "delete", failed_delete())
    reopened = AttemptStore(path)
    pending = reopened.read("vm-one")
    assert pending.phase == "retained"
    assert pending.stages["delete"].outcome == "failed"
    assert "sentinel" not in json.dumps(reopened.snapshot())
    assert reopened.snapshot()["active_attempts"] == 1

    restarted = ExeWorker(work_pool_name="test", observations_path=path)
    await restarted._measure_stage("vm-one", "delete", asyncio.sleep(0))
    assert reopened.read("vm-one").phase == "deleted"
    assert reopened.read("vm-one").error is None
    assert reopened.snapshot()["active_attempts"] == 0


def test_history_is_bounded_without_discarding_active_attempts(tmp_path):
    store = AttemptStore(tmp_path / "observations.sqlite")
    store.save(Attempt(vm="retained", flow_run_id=str(uuid4()), created_at=1, updated_at=1))
    for index in range(1010):
        store.save(
            Attempt(
                vm=f"done-{index}",
                flow_run_id=str(uuid4()),
                created_at=2,
                updated_at=index + 2,
                phase="deleted",
            )
        )
    assert store.read("done-0") is None
    assert store.read("retained") is not None
    snapshot = store.snapshot()
    assert len(snapshot["attempts"]) == 100
    assert snapshot["attempts"][0]["vm"] == "retained"
    assert snapshot["active_attempts"] == 1


def test_image_environment_executes_without_installing_and_does_not_replay(tmp_path):
    payload = {
        "packages": [],
        "runtime": "",
        "config": {
            "environment_mode": "image",
            "requirements": [],
            "flow_run_id": str(uuid4()),
            "env": {},
            "argv": [
                sys.executable,
                "-c",
                "from pathlib import Path; Path('result').write_text('once')",
            ],
            "cwd": str(tmp_path),
            "timeout_seconds": 5,
        },
    }
    root = tmp_path / "execution"
    bootstrap.install(payload, root, Path(sys.prefix))
    bootstrap.install(payload, root, Path(sys.prefix))
    assert (root / "venv").resolve() == Path(sys.prefix).resolve()
    assert set(json.loads((root / "bootstrap-timings.json").read_text())) == {"image_verify"}
    runtime.execute(root)
    assert (tmp_path / "result").read_text() == "once"
    first = json.loads((root / "state.json").read_text())
    runtime.execute(root)
    assert json.loads((root / "state.json").read_text())["finished_at"] == first["finished_at"]
    assert first["exit_code"] == 0
    assert not (root / "config.json").exists()
    assert not (root / "environment").exists()


def test_image_configuration_refuses_runtime_dependency_drift():
    with pytest.raises(ValueError, match="empty requirements"):
        ExeJobConfiguration(image="example:v1", environment_mode="image")
    configured = ExeJobConfiguration(image="example:v1", environment_mode="image", requirements=[])
    assert configured.environment_mode == "image"


def test_inventory_closes_old_absent_attempts_without_claiming_verified_deletion(tmp_path):
    store = AttemptStore(tmp_path / "observations.sqlite")
    for name, updated in [("lost", 1), ("creating", 999), ("present", 1)]:
        store.save(Attempt(vm=name, flow_run_id=str(uuid4()), created_at=1, updated_at=updated))
    store.reconcile_inventory({"present"}, 1000)
    assert store.read("lost").phase == "missing"
    assert store.read("creating").phase == "creating"
    assert store.read("present").phase == "creating"
    assert store.snapshot()["active_attempts"] == 2
