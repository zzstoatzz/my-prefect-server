"""Submit flow runs to exe.dev VMs and reconcile them independently of submission."""

from __future__ import annotations

import asyncio
import hashlib
import shlex
import time
from collections.abc import Awaitable, Callable
from contextlib import suppress
from uuid import UUID

import anyio
from prefect.client.schemas.actions import ArtifactCreate
from prefect.client.schemas.filters import FlowRunFilter, WorkPoolFilter
from prefect.client.schemas.objects import FlowRun
from prefect.exceptions import (
    Abort,
    InfrastructureNotAvailable,
    InfrastructureNotFound,
    ObjectNotFound,
)
from prefect.states import Crashed
from prefect.utilities.engine import propose_state
from prefect.workers.base import BaseJobConfiguration, BaseWorker, BaseWorkerResult
from pydantic import Field

from . import provider
from .client import VM, ExeClient, ExeError, NotFound
from .credentials import ExeCredentials

RUN_TAG = "flow-run-"


class ExeJobConfiguration(BaseJobConfiguration):
    """Configure one execution; credentials must be shared at work-pool scope."""

    credentials: ExeCredentials = Field(default_factory=ExeCredentials)
    timeout_seconds: int = Field(default=1800, ge=30, le=86400)
    requirements: list[str] = Field(default_factory=lambda: ["prefect==3.7.7"])
    local_packages: list[str] = Field(
        default_factory=list,
        description="Wheel paths on the worker host, transferred privately and installed with uv.",
    )
    image: str | None = Field(
        default=None, description="Container image for the VM; unset uses exe.dev's default."
    )
    working_dir: str = "/home/exedev"


class ExeWorkerResult(BaseWorkerResult):
    pass


class ExeWorker(BaseWorker):
    type = "exe"
    job_configuration = ExeJobConfiguration
    _description = "Execute each flow run in a dedicated exe.dev VM."
    _display_name = "exe.dev"

    def __init__(
        self,
        *args,
        run_environment: Callable[[str, int, FlowRun], Awaitable[dict[str, str]]] | None = None,
        release_environment: Callable[[str], Awaitable[None]] | None = None,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self._run_environment = run_environment
        self._release_environment = release_environment
        self._observer_task = None

    async def _measure_stage(self, vm_name: str, stage: str, operation):
        started = time.monotonic()
        outcome = "failed"
        try:
            result = await operation
            outcome = "completed"
            return result
        finally:
            self._logger.info(
                "VM stage vm=%s stage=%s outcome=%s seconds=%.6f",
                vm_name,
                stage,
                outcome,
                time.monotonic() - started,
            )

    @property
    def ownership(self) -> str:
        if not self.work_pool:
            raise RuntimeError("Worker must be registered with a work pool before submission")
        return f"prefect-pool-{self.work_pool.id.hex}"

    @property
    def vm_prefix(self) -> str:
        return f"prefect-{hashlib.sha256(self.ownership.encode()).hexdigest()[:8]}-"

    async def setup(self) -> None:
        await super().setup()
        self._observer_task = asyncio.create_task(self._observe_forever())

    async def teardown(self, *exc_info) -> None:
        if self._observer_task:
            self._observer_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._observer_task
        await super().teardown(*exc_info)

    async def run(
        self, flow_run: FlowRun, configuration: ExeJobConfiguration, task_status=None
    ) -> ExeWorkerResult:
        name = f"{self.vm_prefix}{flow_run.id.hex}-r{flow_run.run_count}"
        tags = [self.ownership, f"{RUN_TAG}{flow_run.id.hex}"]
        async with ExeClient(configuration.credentials.identity_file) as client:
            try:
                vm = await self._measure_stage(
                    name, "create", client.create(name, tags=tags, image=configuration.image)
                )
            except ExeError as creation_error:
                # A lost response and a name conflict both arrive as a failed
                # command. Resolve uncertain creation by identity; never parse
                # error text or adopt an unowned VM.
                try:
                    vm = await client.get(name)
                except NotFound:
                    raise creation_error from None
                self._check_ownership(vm, flow_run.id)
            # Record identity before bootstrap so failed or uncertain submission
            # can be recovered by another observer.
            await self.client.update_flow_run(flow_run.id, infrastructure_pid=name)
            argv = shlex.split(configuration.command or "python -m prefect.engine")
            config = {
                "flow_run_id": str(flow_run.id),
                "argv": argv,
                "env": {k: v for k, v in configuration.env.items() if v is not None},
                "cwd": configuration.working_dir,
                "timeout_seconds": configuration.timeout_seconds,
                "requirements": configuration.requirements,
                "runtime_bin": f"{provider.DIRECTORY}/venv/bin",
                "cgroup": "/sys/fs/cgroup/prefect-flow",
            }
            if self._run_environment:
                config["env"].update(
                    await self._run_environment(name, configuration.timeout_seconds, flow_run)
                )
            await self._measure_stage(
                name,
                "bootstrap",
                provider.install(client, vm, config, local_packages=configuration.local_packages),
            )
            if await provider.service_status(client, vm) is None:
                await self._measure_stage(name, "service_start", provider.start(client, vm))
            if task_status is not None:
                task_status.started(name)
            return ExeWorkerResult(identifier=name, status_code=0)

    async def _initiate_run(self, flow_run: FlowRun, configuration: ExeJobConfiguration) -> None:
        await self.run(flow_run, configuration)

    def _check_ownership(self, vm: VM, flow_run_id: UUID | None = None) -> None:
        if self.ownership not in vm.tags or not vm.name.startswith(self.vm_prefix):
            raise InfrastructureNotAvailable("VM belongs to a different work pool")
        if flow_run_id and f"{RUN_TAG}{flow_run_id.hex}" not in vm.tags:
            raise InfrastructureNotAvailable("VM belongs to a different flow run")

    async def _observe_forever(self) -> None:
        while True:
            try:
                configuration = await self.job_configuration.from_template_and_values(
                    base_job_template=self.work_pool.base_job_template,
                    values={},
                    client=self.client,
                )
                await self.observe_pool(configuration)
            except Exception:
                self._logger.exception(
                    "exe.dev observation failed; retaining infrastructure for next check"
                )
            await anyio.sleep(10)

    async def observe_pool(self, configuration: ExeJobConfiguration) -> None:
        async with ExeClient(configuration.credentials.identity_file) as client:
            vms = await client.list(self.vm_prefix)
            semaphore = asyncio.Semaphore(8)

            async def reconcile_one(vm: VM):
                async with semaphore:
                    try:
                        self._check_ownership(vm)
                        flow_id = UUID(
                            next(
                                tag.removeprefix(RUN_TAG)
                                for tag in vm.tags
                                if tag.startswith(RUN_TAG)
                            )
                        )
                        await self.reconcile(client, vm, flow_id)
                    except NotFound:
                        return
                    except Exception:
                        self._logger.exception("Could not reconcile VM %s; will retry", vm.name)

            await asyncio.gather(*(reconcile_one(vm) for vm in vms))
            await self.reconcile_missing(client)

    async def reconcile_missing(self, client: ExeClient) -> None:
        offset = 0
        while True:
            runs = await self.client.read_flow_runs(
                work_pool_filter=WorkPoolFilter(id={"any_": [self.work_pool.id]}),
                flow_run_filter=FlowRunFilter(state={"type": {"any_": ["PENDING", "RUNNING"]}}),
                limit=100,
                offset=offset,
            )
            for run in runs:
                pid = run.infrastructure_pid
                if not pid or not pid.startswith(self.vm_prefix):
                    continue
                try:
                    await client.get(pid)
                except NotFound:
                    if self._release_environment:
                        await self._release_environment(pid)
                    current = await self.client.read_flow_run(run.id)
                    if current.infrastructure_pid != pid:
                        continue
                    if not current.state or not (
                        current.state.is_pending() or current.state.is_running()
                    ):
                        continue
                    with suppress(Abort):
                        await propose_state(
                            self.client,
                            Crashed(message=f"VM {pid} no longer exists"),
                            flow_run_id=run.id,
                        )
            if len(runs) < 100:
                return
            offset += len(runs)

    async def reconcile(self, client: ExeClient, vm: VM, flow_id: UUID) -> None:
        self._check_ownership(vm, flow_id)
        try:
            flow_run = await self.client.read_flow_run(flow_id)
        except ObjectNotFound:
            self._logger.warning("VM %s has no Prefect flow record", vm.name)
            return
        state = flow_run.state
        if state is None:
            return
        observation = await self._measure_stage(vm.name, "inspect", provider.inspect(client, vm))
        if observation["phase"] == "prepared":
            age = time.time() - vm.created_at.timestamp()
            # Allow the bounded (300s) environment installation to finish before
            # interpreting a missing service as abandoned submission.
            if age < 600:
                return
            if await provider.service_status(client, vm) in {"running", "starting"}:
                return
            observation = {
                "phase": "exited",
                "exit_code": 255,
                "reason": "submission did not start",
                "finished_at": time.time() - 31,
            }
        if observation["phase"] == "running" and not state.is_cancelling():
            if await provider.service_status(client, vm) in {"stopped", "failed"}:
                current = await self.client.read_flow_run(flow_id)
                if current.state is None or current.state.is_cancelling():
                    return
                # Restart only the trusted supervisor. Its persisted-running
                # recovery path stops orphaned children and records a crash;
                # it never replays the flow command.
                await provider.restart(client, vm)
            return
        if observation["phase"] != "exited":
            return
        if self._release_environment:
            await self._release_environment(vm.name)
        # Provider inspection can take long enough for orchestration to advance
        # to another attempt or pause. Base decisions on a fresh API read.
        flow_run = await self.client.read_flow_run(flow_id)
        state = flow_run.state
        if state is None:
            return
        current_attempt = flow_run.infrastructure_pid == vm.name
        if flow_run.infrastructure_pid is None:
            expected_name = f"{self.vm_prefix}{flow_id.hex}-r{flow_run.run_count}"
            current_attempt = vm.name == expected_name
        protected = (
            state.is_final() or state.is_paused() or state.is_scheduled() or state.is_cancelling()
        )
        if current_attempt and not protected:
            # Allow late orchestration state delivery before declaring a crash.
            # A settled run needs no grace period after its process has exited.
            if time.time() - observation["finished_at"] < 30:
                return
            with suppress(Abort):
                await propose_state(
                    self.client,
                    Crashed(
                        message=(
                            f"VM execution ended: {observation['reason']} "
                            f"(exit {observation['exit_code']})"
                        )
                    ),
                    flow_run_id=flow_id,
                )
        # Keep diagnostics if artifact delivery fails; a subsequent observation
        # retries cleanup. Artifact keys identify the exact infrastructure attempt.
        await self._measure_stage(
            vm.name,
            "artifact_delivery",
            self.client.create_artifact(
                ArtifactCreate(
                    key=f"exe-{vm.name}",
                    type="table",
                    flow_run_id=flow_id,
                    description="VM execution outcome and final diagnostic output",
                    data=[observation],
                )
            ),
        )
        self._logger.info(
            "VM cleanup vm=%s seconds_since_execution=%.6f",
            vm.name,
            time.time() - observation["finished_at"],
        )
        await self._measure_stage(vm.name, "delete", client.destroy(vm.name))

    async def kill_infrastructure(
        self,
        infrastructure_pid: str,
        configuration: ExeJobConfiguration,
        grace_seconds: int = 30,
    ) -> None:
        async with ExeClient(configuration.credentials.identity_file) as client:
            try:
                vm = await client.get(infrastructure_pid)
            except NotFound as exc:
                raise InfrastructureNotFound(infrastructure_pid) from exc
            self._check_ownership(vm)
            if await provider.service_status(client, vm) is None:
                # Cancellation can arrive while uv is still bootstrapping.
                # Destroying the owned VM stops that installation too;
                # it must not later start a supervisor for a cancelled run.
                if self._release_environment:
                    await self._release_environment(infrastructure_pid)
                await client.destroy(vm.name)
                return
            await provider.stop(client, vm, grace_seconds)
            if await provider.service_status(client, vm) not in {"stopped", "failed"}:
                raise InfrastructureNotAvailable("VM supervisor has not stopped yet")
            if self._release_environment:
                await self._release_environment(infrastructure_pid)
