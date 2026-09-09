"""Submission and reconciliation contracts with provider I/O replaced."""

import datetime as dt
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from prefect.client.schemas.objects import FlowRun, WorkPool
from prefect.exceptions import InfrastructureNotAvailable
from prefect.states import Cancelled, Cancelling, Completed, Paused, Running, Scheduled
from prefect_sprites import SpritesJobConfiguration, SpritesWorker, provider, worker as module
from sprites.exceptions import APIError, NetworkError, NotFoundError, SpriteError


@pytest.fixture
def harness(monkeypatch):
    worker = SpritesWorker(work_pool_name="test-sprites")
    worker._work_pool = WorkPool(name="test-sprites", type="sprites")
    worker._client = SimpleNamespace(
        update_flow_run=AsyncMock(),
        read_flow_run=AsyncMock(),
        create_artifact=AsyncMock(),
        read_flow_runs=AsyncMock(return_value=[]),
    )
    flow_run = FlowRun(flow_id=uuid4(), state=Running())
    sprite = SimpleNamespace(
        name=f"{worker.sprite_prefix}{flow_run.id.hex}-r0",
        labels=[worker.ownership, f"flow-run:{flow_run.id}"],
        destroy=AsyncMock(),
        get_service=AsyncMock(side_effect=NotFoundError("not installed")),
        created_at=dt.datetime.now(dt.UTC) - dt.timedelta(minutes=10),
    )
    flow_run.infrastructure_pid = sprite.name
    worker._client.read_flow_run.return_value = flow_run
    client = SimpleNamespace(
        create_sprite=AsyncMock(return_value=sprite), get_sprite=AsyncMock(return_value=sprite)
    )
    context = AsyncMock()
    context.__aenter__.return_value = client
    monkeypatch.setattr(module, "AsyncSpritesClient", Mock(return_value=context))
    monkeypatch.setattr(provider, "install", AsyncMock())
    monkeypatch.setattr(provider, "start", AsyncMock())
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
    return worker, sprite, flow_run, client


@pytest.mark.asyncio
async def test_submission_returns_without_waiting_for_execution(harness):
    worker, sprite, flow_run, _client = harness
    status = Mock()
    result = await worker.run(
        flow_run, SpritesJobConfiguration(credentials={"token": "test"}), status
    )
    assert result.status_code == 0
    status.started.assert_called_once_with(sprite.name)
    worker.client.update_flow_run.assert_awaited_once_with(
        flow_run.id, infrastructure_pid=sprite.name
    )
    provider.start.assert_awaited_once_with(sprite)
    config = provider.install.call_args.args[1]
    assert config["argv"] == ["python", "-m", "prefect.engine"]
    assert config["requirements"] == ["prefect==3.7.7"]
    assert config["runtime_bin"] == f"{provider.DIRECTORY}/venv/bin"
    provider.inspect.assert_not_called()
    sprite.destroy.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("creation_error", [NetworkError("response lost"), SpriteError("conflict")])
async def test_uncertain_create_reconciles_same_name(harness, creation_error):
    worker, sprite, flow_run, client = harness
    client.create_sprite.side_effect = creation_error
    sprite.get_service.side_effect = None
    await worker.run(flow_run, SpritesJobConfiguration(credentials={"token": "test"}))
    client.get_sprite.assert_awaited_once_with(sprite.name)
    client.create_sprite.assert_awaited_once()
    provider.start.assert_not_called()


@pytest.mark.asyncio
async def test_failed_create_without_existing_sprite_preserves_error(harness):
    worker, _sprite, flow_run, client = harness
    failure = SpriteError("provider refused creation")
    client.create_sprite.side_effect = failure
    client.get_sprite.side_effect = NotFoundError("absent")
    with pytest.raises(SpriteError) as caught:
        await worker.run(flow_run, SpritesJobConfiguration(credentials={"token": "test"}))
    assert caught.value is failure
    provider.install.assert_not_called()


@pytest.mark.asyncio
async def test_stale_attempt_never_crashes_new_attempt(harness):
    worker, sprite, flow_run, _client = harness
    flow_run.infrastructure_pid = "new-attempt"
    await worker.reconcile(sprite, flow_run.id)
    module.propose_state.assert_not_called()
    sprite.destroy.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("state", [Completed(), Cancelled(), Cancelling(), Scheduled(), Paused()])
async def test_reconciliation_protects_orchestration_states(harness, state):
    worker, sprite, flow_run, _client = harness
    flow_run.state = state
    await worker.reconcile(sprite, flow_run.id)
    module.propose_state.assert_not_called()
    sprite.destroy.assert_awaited_once()


@pytest.mark.asyncio
async def test_diagnostics_precede_deletion_and_retry_on_failure(harness):
    worker, sprite, flow_run, _client = harness
    worker.client.create_artifact.side_effect = RuntimeError("API unavailable")
    with pytest.raises(RuntimeError, match="API unavailable"):
        await worker.reconcile(sprite, flow_run.id)
    sprite.destroy.assert_not_called()
    worker.client.create_artifact.side_effect = None
    await worker.reconcile(sprite, flow_run.id)
    sprite.destroy.assert_awaited_once()


@pytest.mark.asyncio
async def test_unknown_provider_status_does_not_kill_or_restart(harness):
    worker, sprite, flow_run, _client = harness
    provider.inspect.side_effect = NetworkError("temporary outage")
    with pytest.raises(NetworkError):
        await worker.reconcile(sprite, flow_run.id)
    sprite.destroy.assert_not_called()
    provider.start.assert_not_called()
    module.propose_state.assert_not_called()


@pytest.mark.asyncio
async def test_foreign_pool_resource_is_never_deleted(harness):
    worker, sprite, flow_run, _client = harness
    sprite.labels = ["prefect-pool:another-pool", f"flow-run:{flow_run.id}"]
    with pytest.raises(InfrastructureNotAvailable):
        await worker.reconcile(sprite, flow_run.id)
    sprite.destroy.assert_not_called()


@pytest.mark.asyncio
async def test_abandoned_bootstrap_is_reconciled(harness):
    worker, sprite, flow_run, _client = harness
    provider.inspect.return_value = {"phase": "prepared"}
    await worker.reconcile(sprite, flow_run.id)
    module.propose_state.assert_awaited_once()
    sprite.destroy.assert_awaited_once()


@pytest.mark.asyncio
async def test_new_worker_rediscovers_previous_submission(harness):
    worker, sprite, _flow_run, client = harness
    replacement = SpritesWorker(work_pool_name="test-sprites")
    replacement._work_pool = worker.work_pool
    replacement._client = worker.client
    client.list_sprites = AsyncMock(
        return_value=SimpleNamespace(sprites=[SimpleNamespace(name=sprite.name)], has_more=False)
    )
    await replacement.observe_pool(SpritesJobConfiguration(credentials={"token": "test"}))
    assert worker.client.read_flow_run.await_count == 2
    sprite.destroy.assert_awaited_once()
    client.create_sprite.assert_not_called()


@pytest.mark.asyncio
async def test_cancellation_stops_service_and_retains_artifacts(harness):
    worker, sprite, _flow_run, _client = harness
    sprite.stop_service = AsyncMock()
    sprite.get_service.side_effect = None
    sprite.get_service.return_value = SimpleNamespace(state=SimpleNamespace(status="stopped"))
    await worker.kill_infrastructure(
        sprite.name, SpritesJobConfiguration(credentials={"token": "test"})
    )
    sprite.stop_service.assert_awaited_once_with(provider.SERVICE, timeout=30)
    sprite.destroy.assert_not_called()


def test_worker_template_declares_credential_block():
    template = SpritesWorker.get_default_base_job_template()
    assert "credentials" in template["variables"]["properties"]
    assert "token" not in template["variables"]["properties"]
    assert template["job_configuration"]["credentials"] == "{{ credentials }}"


@pytest.mark.asyncio
async def test_cancellation_requires_confirmed_service_stop(harness):
    worker, sprite, _flow_run, _client = harness
    sprite.stop_service = AsyncMock()
    sprite.get_service.side_effect = None
    sprite.get_service.return_value = SimpleNamespace(state=None)
    with pytest.raises(InfrastructureNotAvailable, match="has not stopped"):
        await worker.kill_infrastructure(
            sprite.name, SpritesJobConfiguration(credentials={"token": "test"})
        )
    sprite.destroy.assert_not_called()


@pytest.mark.asyncio
async def test_attempt_changed_during_inspection_is_protected(harness):
    worker, sprite, flow_run, _client = harness
    replacement = flow_run.model_copy(update={"infrastructure_pid": "replacement"})
    worker.client.read_flow_run.side_effect = [flow_run, replacement]
    await worker.reconcile(sprite, flow_run.id)
    module.propose_state.assert_not_called()
    sprite.destroy.assert_awaited_once()


@pytest.mark.asyncio
async def test_missing_sprite_is_confirmed_before_crashing_run(harness):
    worker, sprite, flow_run, client = harness
    worker.client.read_flow_runs.return_value = [flow_run]
    client.get_sprite.side_effect = NotFoundError("deleted")
    await worker.reconcile_missing(client)
    client.get_sprite.assert_awaited_once_with(sprite.name)
    module.propose_state.assert_awaited_once()


@pytest.mark.asyncio
async def test_missing_check_network_error_is_not_a_crash(harness):
    worker, _sprite, flow_run, client = harness
    worker.client.read_flow_runs.return_value = [flow_run]
    client.get_sprite.side_effect = NetworkError("temporary outage")
    with pytest.raises(NetworkError):
        await worker.reconcile_missing(client)
    module.propose_state.assert_not_called()


@pytest.mark.asyncio
async def test_provider_payload_never_enters_exec_url():
    # Call the real adapter, independently of the fixture replacing provider I/O.
    import importlib

    real = importlib.reload(provider)
    sprite = SimpleNamespace(command=Mock(return_value=SimpleNamespace(output=AsyncMock())))
    await real.install(sprite, {"env": {"PREFECT_API_AUTH_STRING": "test-auth-value"}})
    args, kwargs = sprite.command.call_args
    assert "test-auth-value" not in repr(args)
    assert "env" not in kwargs
    payload = json.loads(kwargs["stdin"].getvalue())
    assert payload["config"]["env"]["PREFECT_API_AUTH_STRING"] == "test-auth-value"


@pytest.mark.asyncio
async def test_sdk_service_404_starts_runtime(harness):
    worker, sprite, flow_run, _client = harness
    sprite.get_service.side_effect = APIError("missing", status_code=404)
    await worker.run(flow_run, SpritesJobConfiguration(credentials={"token": "test"}))
    provider.start.assert_awaited_once_with(sprite)


@pytest.mark.asyncio
async def test_sdk_service_outage_does_not_start_duplicate(harness):
    worker, sprite, flow_run, _client = harness
    sprite.get_service.side_effect = APIError("outage", status_code=503)
    with pytest.raises(APIError):
        await worker.run(flow_run, SpritesJobConfiguration(credentials={"token": "test"}))
    provider.start.assert_not_called()


@pytest.mark.asyncio
async def test_worker_injects_grant_only_into_private_submission(harness):
    worker, sprite, flow_run, _client = harness
    worker._run_environment = AsyncMock(return_value={"PHI_INFERENCE_TOKEN": "attempt-token"})
    configuration = SpritesJobConfiguration(credentials={"token": "test"})
    await worker.run(flow_run, configuration)
    worker._run_environment.assert_awaited_once_with(sprite.name, configuration.timeout_seconds)
    assert provider.install.call_args.args[1]["env"]["PHI_INFERENCE_TOKEN"] == "attempt-token"
    assert "PHI_INFERENCE_TOKEN" not in configuration.env
    assert "attempt-token" not in str(worker.client.update_flow_run.call_args)


@pytest.mark.asyncio
async def test_exited_run_revokes_grant_even_when_artifact_write_fails(harness):
    worker, sprite, flow_run, _client = harness
    worker._release_environment = AsyncMock()
    worker.client.create_artifact.side_effect = RuntimeError("API unavailable")
    with pytest.raises(RuntimeError, match="API unavailable"):
        await worker.reconcile(sprite, flow_run.id)
    worker._release_environment.assert_awaited_once_with(sprite.name)
    sprite.destroy.assert_not_called()


@pytest.mark.asyncio
async def test_local_wheel_is_transferred_only_through_stdin(tmp_path):
    import base64
    import importlib

    real = importlib.reload(provider)
    wheel = tmp_path / "example-0.1-py3-none-any.whl"
    wheel.write_bytes(b"wheel-content-sentinel")
    sprite = SimpleNamespace(command=Mock(return_value=SimpleNamespace(output=AsyncMock())))
    await real.install(sprite, {"requirements": []}, local_packages=[str(wheel)])
    args, kwargs = sprite.command.call_args
    assert str(wheel) not in repr(args)
    assert "wheel-content-sentinel" not in repr(args)
    package = json.loads(kwargs["stdin"].getvalue())["packages"][0]
    assert package["name"] == wheel.name
    assert base64.b64decode(package["data"]) == wheel.read_bytes()


@pytest.mark.asyncio
async def test_duplicate_wheel_names_are_rejected_before_submission(tmp_path):
    import importlib

    real = importlib.reload(provider)
    wheel = tmp_path / "example.whl"
    wheel.touch()
    sprite = SimpleNamespace(command=Mock())
    with pytest.raises(ValueError, match="unique filenames"):
        await real.install(sprite, {}, local_packages=[str(wheel), str(wheel)])
    sprite.command.assert_not_called()
