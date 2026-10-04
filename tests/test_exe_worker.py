"""Submission and reconciliation contracts with exe.dev I/O replaced."""

import asyncio
import base64
import datetime as dt
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from prefect.client.schemas.objects import FlowRun, WorkPool
from prefect.exceptions import InfrastructureNotAvailable, InfrastructureNotFound
from prefect.states import Cancelled, Cancelling, Completed, Paused, Running, Scheduled
from prefect_exe import ExeJobConfiguration, ExeWorker, provider, worker as module
from prefect_exe.client import CONTROL_HOST, VM, ExeClient, ExeError, NotFound


@pytest.fixture
def harness(monkeypatch):
    worker = ExeWorker(work_pool_name="test-exe")
    worker._work_pool = WorkPool(name="test-exe", type="exe")
    worker._client = SimpleNamespace(
        update_flow_run=AsyncMock(),
        read_flow_run=AsyncMock(),
        create_artifact=AsyncMock(),
        read_flow_runs=AsyncMock(return_value=[]),
    )
    flow_run = FlowRun(flow_id=uuid4(), state=Running())
    name = f"{worker.vm_prefix}{flow_run.id.hex}-r0"
    vm = VM(
        name=name,
        host=f"{name}.exe.xyz",
        tags=(worker.ownership, f"flow-run-{flow_run.id.hex}"),
        created_at=dt.datetime.now(dt.UTC) - dt.timedelta(minutes=10),
    )
    flow_run.infrastructure_pid = vm.name
    worker._client.read_flow_run.return_value = flow_run
    client = SimpleNamespace(
        create=AsyncMock(return_value=vm),
        get=AsyncMock(return_value=vm),
        list=AsyncMock(return_value=[vm]),
        destroy=AsyncMock(),
    )
    context = AsyncMock()
    context.__aenter__.return_value = client
    monkeypatch.setattr(module, "ExeClient", Mock(return_value=context))
    monkeypatch.setattr(provider, "install", AsyncMock())
    monkeypatch.setattr(provider, "start", AsyncMock())
    monkeypatch.setattr(provider, "restart", AsyncMock())
    monkeypatch.setattr(provider, "stop", AsyncMock())
    monkeypatch.setattr(provider, "service_status", AsyncMock(return_value=None))
    monkeypatch.setattr(
        provider,
        "inspect",
        AsyncMock(
            return_value={
                "phase": "exited",
                "exit_code": 17,
                "reason": "process exited",
                "finished_at": 1,
            }
        ),
    )
    monkeypatch.setattr(module, "propose_state", AsyncMock())
    return worker, vm, flow_run, client


async def test_submission_returns_without_waiting_for_execution(harness):
    worker, vm, flow_run, client = harness
    status = Mock()
    result = await worker.run(flow_run, ExeJobConfiguration(), status)
    assert result.status_code == 0
    status.started.assert_called_once_with(vm.name)
    client.create.assert_awaited_once_with(vm.name, tags=list(vm.tags), image=None)
    worker.client.update_flow_run.assert_awaited_once_with(flow_run.id, infrastructure_pid=vm.name)
    provider.start.assert_awaited_once_with(client, vm)
    config = provider.install.call_args.args[2]
    assert config["argv"] == ["python", "-m", "prefect.engine"]
    assert config["requirements"] == ["prefect==3.7.7"]
    assert config["runtime_bin"] == f"{provider.DIRECTORY}/venv/bin"
    provider.inspect.assert_not_called()
    client.destroy.assert_not_called()


async def test_uncertain_create_reconciles_same_name(harness):
    worker, vm, flow_run, client = harness
    client.create.side_effect = ExeError(255, "connection lost")
    provider.service_status.return_value = "running"
    await worker.run(flow_run, ExeJobConfiguration())
    client.get.assert_awaited_once_with(vm.name)
    client.create.assert_awaited_once()
    provider.start.assert_not_called()


async def test_failed_create_without_existing_vm_preserves_error(harness):
    worker, vm, flow_run, client = harness
    failure = ExeError(1, "provider refused creation")
    client.create.side_effect = failure
    client.get.side_effect = NotFound(vm.name)
    with pytest.raises(ExeError) as caught:
        await worker.run(flow_run, ExeJobConfiguration())
    assert caught.value is failure
    provider.install.assert_not_called()


async def test_name_conflict_with_unowned_vm_is_not_adopted(harness):
    worker, vm, flow_run, client = harness
    client.create.side_effect = ExeError(1, "VM name is not available")
    client.get.return_value = replace(vm, tags=("someone-else",))
    with pytest.raises(InfrastructureNotAvailable):
        await worker.run(flow_run, ExeJobConfiguration())
    provider.install.assert_not_called()


async def test_stale_attempt_never_crashes_new_attempt(harness):
    worker, vm, flow_run, client = harness
    flow_run.infrastructure_pid = "new-attempt"
    await worker.reconcile(client, vm, flow_run.id)
    module.propose_state.assert_not_called()
    client.destroy.assert_awaited_once_with(vm.name)


@pytest.mark.parametrize("state", [Completed(), Cancelled(), Cancelling(), Scheduled(), Paused()])
async def test_reconciliation_protects_orchestration_states(harness, state):
    worker, vm, flow_run, client = harness
    flow_run.state = state
    await worker.reconcile(client, vm, flow_run.id)
    module.propose_state.assert_not_called()
    client.destroy.assert_awaited_once()


async def test_diagnostics_precede_deletion_and_retry_on_failure(harness):
    worker, vm, flow_run, client = harness
    worker.client.create_artifact.side_effect = RuntimeError("API unavailable")
    with pytest.raises(RuntimeError, match="API unavailable"):
        await worker.reconcile(client, vm, flow_run.id)
    client.destroy.assert_not_called()
    worker.client.create_artifact.side_effect = None
    await worker.reconcile(client, vm, flow_run.id)
    client.destroy.assert_awaited_once()


async def test_unknown_provider_status_does_not_kill_or_restart(harness):
    worker, vm, flow_run, client = harness
    provider.inspect.side_effect = ExeError(255, "temporary outage")
    with pytest.raises(ExeError):
        await worker.reconcile(client, vm, flow_run.id)
    client.destroy.assert_not_called()
    provider.start.assert_not_called()
    module.propose_state.assert_not_called()


async def test_foreign_pool_resource_is_never_deleted(harness):
    worker, vm, flow_run, client = harness
    foreign = replace(vm, tags=("prefect-pool-another", f"flow-run-{flow_run.id.hex}"))
    with pytest.raises(InfrastructureNotAvailable):
        await worker.reconcile(client, foreign, flow_run.id)
    client.destroy.assert_not_called()


async def test_abandoned_bootstrap_is_reconciled(harness):
    worker, vm, flow_run, client = harness
    provider.inspect.return_value = {"phase": "prepared"}
    await worker.reconcile(client, vm, flow_run.id)
    module.propose_state.assert_awaited_once()
    client.destroy.assert_awaited_once()


async def test_recent_bootstrap_is_left_alone(harness):
    worker, vm, flow_run, client = harness
    provider.inspect.return_value = {"phase": "prepared"}
    await worker.reconcile(client, replace(vm, created_at=dt.datetime.now(dt.UTC)), flow_run.id)
    module.propose_state.assert_not_called()
    client.destroy.assert_not_called()


async def test_new_worker_rediscovers_previous_submission(harness):
    worker, _vm, _flow_run, client = harness
    replacement = ExeWorker(work_pool_name="test-exe")
    replacement._work_pool = worker.work_pool
    replacement._client = worker.client
    await replacement.observe_pool(ExeJobConfiguration())
    client.list.assert_awaited_once_with(worker.vm_prefix)
    assert worker.client.read_flow_run.await_count == 2
    client.destroy.assert_awaited_once()
    client.create.assert_not_called()


async def test_cancellation_stops_service_and_retains_artifacts(harness):
    worker, vm, _flow_run, client = harness
    provider.service_status.side_effect = ["running", "stopped"]
    await worker.kill_infrastructure(vm.name, ExeJobConfiguration())
    provider.stop.assert_awaited_once_with(client, vm, 30)
    client.destroy.assert_not_called()


def test_worker_template_declares_credential_block():
    template = ExeWorker.get_default_base_job_template()
    assert "credentials" in template["variables"]["properties"]
    assert "identity_file" not in template["variables"]["properties"]
    assert template["job_configuration"]["credentials"] == "{{ credentials }}"


@pytest.mark.parametrize("after_stop", ["running", "stopping"])
async def test_cancellation_requires_confirmed_service_stop(harness, after_stop):
    worker, vm, _flow_run, client = harness
    provider.service_status.side_effect = ["running", after_stop]
    with pytest.raises(InfrastructureNotAvailable, match="has not stopped"):
        await worker.kill_infrastructure(vm.name, ExeJobConfiguration())
    client.destroy.assert_not_called()


async def test_cancelling_a_deleted_vm_reports_missing_infrastructure(harness):
    worker, vm, _flow_run, client = harness
    client.get.side_effect = NotFound(vm.name)
    with pytest.raises(InfrastructureNotFound):
        await worker.kill_infrastructure(vm.name, ExeJobConfiguration())


async def test_attempt_changed_during_inspection_is_protected(harness):
    worker, vm, flow_run, client = harness
    replacement = flow_run.model_copy(update={"infrastructure_pid": "replacement"})
    worker.client.read_flow_run.side_effect = [flow_run, replacement]
    await worker.reconcile(client, vm, flow_run.id)
    module.propose_state.assert_not_called()
    client.destroy.assert_awaited_once()


async def test_missing_vm_is_confirmed_before_crashing_run(harness):
    worker, vm, flow_run, client = harness
    worker.client.read_flow_runs.return_value = [flow_run]
    client.get.side_effect = NotFound(vm.name)
    await worker.reconcile_missing(client)
    client.get.assert_awaited_once_with(vm.name)
    module.propose_state.assert_awaited_once()


async def test_missing_check_transport_error_is_not_a_crash(harness):
    worker, _vm, flow_run, client = harness
    worker.client.read_flow_runs.return_value = [flow_run]
    client.get.side_effect = ExeError(255, "temporary outage")
    with pytest.raises(ExeError):
        await worker.reconcile_missing(client)
    module.propose_state.assert_not_called()


async def test_service_check_outage_does_not_start_duplicate(harness):
    worker, _vm, flow_run, _client = harness
    provider.service_status.side_effect = ExeError(255, "outage")
    with pytest.raises(ExeError):
        await worker.run(flow_run, ExeJobConfiguration())
    provider.start.assert_not_called()


async def test_worker_injects_grant_only_into_private_submission(harness):
    worker, vm, flow_run, _client = harness
    worker._run_environment = AsyncMock(return_value={"PHI_INFERENCE_TOKEN": "attempt-token"})
    configuration = ExeJobConfiguration()
    await worker.run(flow_run, configuration)
    worker._run_environment.assert_awaited_once_with(
        vm.name, configuration.timeout_seconds, flow_run
    )
    assert provider.install.call_args.args[2]["env"]["PHI_INFERENCE_TOKEN"] == "attempt-token"
    assert "PHI_INFERENCE_TOKEN" not in configuration.env
    assert "attempt-token" not in str(worker.client.update_flow_run.call_args)


async def test_exited_run_revokes_grant_even_when_artifact_write_fails(harness):
    worker, vm, flow_run, client = harness
    worker._release_environment = AsyncMock()
    worker.client.create_artifact.side_effect = RuntimeError("API unavailable")
    with pytest.raises(RuntimeError, match="API unavailable"):
        await worker.reconcile(client, vm, flow_run.id)
    worker._release_environment.assert_awaited_once_with(vm.name)
    client.destroy.assert_not_called()


async def test_stage_timing_preserves_failure_without_logging_secret(harness, caplog):
    worker, vm, _run, _client = harness

    async def failed_operation():
        raise RuntimeError("private-operation-payload")

    with caplog.at_level("INFO"), pytest.raises(RuntimeError, match="private-operation-payload"):
        await worker._measure_stage(vm.name, "create", failed_operation())
    assert "stage=create outcome=failed seconds=" in caplog.text
    assert "private-operation-payload" not in caplog.text


@pytest.mark.parametrize("settled", [True, False])
async def test_recent_exit_waits_only_for_unsettled_run(harness, settled):
    worker, vm, flow_run, client = harness
    flow_run.state = Completed() if settled else Running()
    provider.inspect.return_value["finished_at"] = module.time.time()
    await worker.reconcile(client, vm, flow_run.id)
    module.propose_state.assert_not_awaited()
    if settled:
        worker.client.create_artifact.assert_awaited_once()
        client.destroy.assert_awaited_once()
    else:
        worker.client.create_artifact.assert_not_awaited()
        client.destroy.assert_not_awaited()


async def test_slow_vm_does_not_block_other_reconciliation(harness):
    worker, vm, _flow_run, client = harness
    slow = replace(vm, name=vm.name + "-slow")
    slow_started = asyncio.Event()
    release_slow = asyncio.Event()
    fast_completed = asyncio.Event()
    client.list.return_value = [slow, vm]

    async def reconcile(_client, current, _flow_id):
        if current.name == slow.name:
            slow_started.set()
            await release_slow.wait()
        else:
            fast_completed.set()

    worker.reconcile = reconcile
    worker.reconcile_missing = AsyncMock()
    observing = asyncio.create_task(worker.observe_pool(ExeJobConfiguration()))
    try:
        await asyncio.wait_for(slow_started.wait(), 1)
        await asyncio.wait_for(fast_completed.wait(), 1)
        assert not observing.done()
    finally:
        release_slow.set()
        await observing


async def test_observation_concurrency_is_bounded_and_cancelled(harness):
    worker, vm, _flow_run, client = harness
    full = asyncio.Event()
    blocked = asyncio.Event()
    active = 0
    peak = 0
    client.list.return_value = [vm for _ in range(20)]

    async def reconcile(_client, _vm, _flow_id):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        if active == 8:
            full.set()
        try:
            await blocked.wait()
        finally:
            active -= 1

    worker.reconcile = reconcile
    observing = asyncio.create_task(worker.observe_pool(ExeJobConfiguration()))
    try:
        await asyncio.wait_for(full.wait(), 1)
        assert peak == 8
    finally:
        observing.cancel()
        with pytest.raises(asyncio.CancelledError):
            await observing
    assert active == 0


async def test_listing_rejects_unowned_vm_before_inspecting_it(harness):
    worker, vm, _flow_run, client = harness
    client.list.return_value = [replace(vm, tags=("another-pool",))]
    worker.reconcile_missing = AsyncMock()
    await worker.observe_pool(ExeJobConfiguration())
    provider.inspect.assert_not_awaited()
    client.destroy.assert_not_awaited()


@pytest.mark.parametrize(
    "status,expected", [("stopped", 1), ("failed", 1), ("running", 0), ("starting", 0)]
)
async def test_stopped_supervisor_recovery_does_not_replay_flow(harness, status, expected):
    worker, vm, flow_run, client = harness
    provider.inspect.return_value = {"phase": "running"}
    provider.service_status.return_value = status
    await worker.reconcile(client, vm, flow_run.id)
    assert provider.restart.await_count == expected
    provider.install.assert_not_awaited()
    provider.start.assert_not_awaited()
    client.destroy.assert_not_awaited()


async def test_cancellation_arriving_during_service_check_prevents_restart(harness):
    worker, vm, flow_run, client = harness
    provider.inspect.return_value = {"phase": "running"}

    async def stopped_service(_client, _vm):
        worker.client.read_flow_run.return_value = flow_run.model_copy(
            update={"state": Cancelling()}
        )
        return "stopped"

    provider.service_status.side_effect = stopped_service
    await worker.reconcile(client, vm, flow_run.id)
    provider.restart.assert_not_awaited()
    client.destroy.assert_not_awaited()


async def test_cancellation_during_bootstrap_deletes_vm_and_revokes_grant(harness):
    worker, vm, _flow_run, client = harness
    worker._release_environment = AsyncMock()
    await worker.kill_infrastructure(vm.name, ExeJobConfiguration())
    worker._release_environment.assert_awaited_once_with(vm.name)
    client.destroy.assert_awaited_once_with(vm.name)
    provider.stop.assert_not_awaited()
    provider.start.assert_not_awaited()


@pytest.fixture
def remote():
    vm = VM(name="vm", host="vm.exe.xyz", tags=(), created_at=dt.datetime.now(dt.UTC))
    return SimpleNamespace(run=AsyncMock(return_value="")), vm


async def test_provider_payload_travels_only_on_stdin(remote):
    client, vm = remote
    await provider.install(client, vm, {"env": {"PREFECT_API_AUTH_STRING": "test-auth-value"}})
    args, kwargs = client.run.call_args
    assert "test-auth-value" not in repr(args)
    payload = json.loads(kwargs["stdin"])
    assert payload["config"]["env"]["PREFECT_API_AUTH_STRING"] == "test-auth-value"
    assert "lease" not in payload["runtime"]


async def test_local_wheel_is_transferred_only_through_stdin(remote, tmp_path):
    client, vm = remote
    wheel = tmp_path / "example-0.1-py3-none-any.whl"
    wheel.write_bytes(b"wheel-content-sentinel")
    await provider.install(client, vm, {"requirements": []}, local_packages=[str(wheel)])
    args, kwargs = client.run.call_args
    assert str(wheel) not in repr(args)
    assert "wheel-content-sentinel" not in repr(args)
    package = json.loads(kwargs["stdin"])["packages"][0]
    assert package["name"] == wheel.name
    assert base64.b64decode(package["data"]) == wheel.read_bytes()


async def test_duplicate_wheel_names_are_rejected_before_submission(remote, tmp_path):
    client, vm = remote
    wheel = tmp_path / "example.whl"
    wheel.touch()
    with pytest.raises(ValueError, match="unique filenames"):
        await provider.install(client, vm, {}, local_packages=[str(wheel), str(wheel)])
    client.run.assert_not_called()


async def test_inspection_distinguishes_missing_state_from_recorded_outcome(remote):
    client, vm = remote
    assert await provider.inspect(client, vm) == {"phase": "prepared"}
    client.run.return_value = '{"phase":"exited","exit_code":0}'
    assert (await provider.inspect(client, vm))["exit_code"] == 0
    client.run.return_value = '{"phase":"unknown"}'
    with pytest.raises(ValueError, match="invalid execution phase"):
        await provider.inspect(client, vm)


@pytest.mark.parametrize(
    "load,active,expected",
    [
        ("not-found", "inactive", None),
        ("loaded", "active", "running"),
        ("loaded", "activating", "starting"),
        ("loaded", "deactivating", "stopping"),
        ("loaded", "inactive", "stopped"),
        ("loaded", "failed", "failed"),
    ],
)
async def test_service_status_reads_systemd_state(remote, load, active, expected):
    client, vm = remote
    client.run.return_value = f"LoadState={load}\nActiveState={active}\n"
    assert await provider.service_status(client, vm) == expected


async def test_start_installs_unit_from_stdin_without_waiting_for_the_flow(remote):
    client, vm = remote
    await provider.start(client, vm)
    args, kwargs = client.run.call_args
    assert "--no-block" in args[1]
    assert f"{provider.DIRECTORY}/runtime.py" in kwargs["stdin"].decode()


@pytest.fixture
def control(monkeypatch):
    calls = []
    responses = []

    async def ssh(self, host, command, **kwargs):
        calls.append((host, command))
        response = responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    monkeypatch.setattr(ExeClient, "_ssh", ssh)
    return calls, responses


def listing(*names):
    return json.dumps(
        {
            "vms": [
                {
                    "vm_name": name,
                    "ssh_dest": f"{name}.exe.xyz",
                    "tags": ["a"],
                    "created_at": "2026-10-04T07:00:44Z",
                }
                for name in names
            ]
        }
    )


async def test_listing_filters_by_prefix_and_parses_identity(control):
    calls, responses = control
    responses.append(listing("prefect-1-a", "personal-box"))
    async with ExeClient() as client:
        (vm,) = await client.list("prefect-1-")
    assert calls == [(CONTROL_HOST, "ls --json")]
    assert vm == VM(
        name="prefect-1-a",
        host="prefect-1-a.exe.xyz",
        tags=("a",),
        created_at=dt.datetime(2026, 10, 4, 7, 0, 44, tzinfo=dt.UTC),
    )


async def test_get_requires_an_exact_name(control):
    _calls, responses = control
    responses.append(listing("prefect-1-a-longer"))
    async with ExeClient() as client:
        with pytest.raises(NotFound):
            await client.get("prefect-1-a")


async def test_destroy_confirms_absence(control):
    calls, responses = control
    responses.extend(["deleted", listing("vm")])
    async with ExeClient() as client:
        with pytest.raises(ExeError, match="still exists"):
            await client.destroy("vm")
        responses.extend(['VM "vm" not found', listing()])
        await client.destroy("vm")
    assert calls[0] == (CONTROL_HOST, "rm vm")


async def test_ssh_pins_the_host_key_and_reports_failure(monkeypatch, tmp_path):
    recorded = {}

    async def spawn(*argv, **kwargs):
        recorded["argv"] = argv
        known_hosts = next(a for a in argv if a.startswith("UserKnownHostsFile=")).split("=", 1)[1]
        recorded["known_hosts"] = Path(known_hosts).read_text()
        return SimpleNamespace(
            communicate=AsyncMock(return_value=(b"denied", None)), returncode=255
        )

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    identity = tmp_path / "key"
    async with ExeClient(identity) as client:
        with pytest.raises(ExeError) as caught:
            await client.list()
    assert caught.value.returncode == 255
    argv = recorded["argv"]
    assert "StrictHostKeyChecking=yes" in argv and "BatchMode=yes" in argv
    assert argv[argv.index("-i") + 1] == str(identity)
    assert recorded["known_hosts"].startswith("exe.dev,*.exe.xyz ssh-rsa ")
