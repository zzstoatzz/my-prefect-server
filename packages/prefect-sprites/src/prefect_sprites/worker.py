"""Submit flow runs to Sprites and reconcile them independently of submission."""

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
from sprites import AsyncSpritesClient, ListOptions, URLSettings
from sprites.exceptions import NotFoundError, SpriteError

from . import provider
from .credentials import SpritesCredentials


class SpritesJobConfiguration(BaseJobConfiguration):
    """Configure one execution; credentials must be shared at work-pool scope."""

    credentials: SpritesCredentials = Field(default_factory=SpritesCredentials)
    timeout_seconds: int = Field(default=1800, ge=30, le=86400)
    requirements: list[str] = Field(default_factory=lambda: ["prefect==3.7.7"])
    local_packages: list[str] = Field(
        default_factory=list,
        description="Wheel paths on the worker host, transferred privately and installed with uv.",
    )
    working_dir: str = "/home/sprite"


class SpritesWorkerResult(BaseWorkerResult):
    pass


class SpritesWorker(BaseWorker):
    type = "sprites"
    job_configuration = SpritesJobConfiguration
    _description = "Execute each flow run in a dedicated Fly Sprite."
    _display_name = "Fly Sprites"

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

    async def _measure_stage(self, sprite_name: str, stage: str, operation):
        started = time.monotonic()
        outcome = "failed"
        try:
            result = await operation
            outcome = "completed"
            return result
        finally:
            self._logger.info(
                "Sprite stage sprite=%s stage=%s outcome=%s seconds=%.6f",
                sprite_name,
                stage,
                outcome,
                time.monotonic() - started,
            )

    @property
    def ownership(self) -> str:
        if not self.work_pool:
            raise RuntimeError("Worker must be registered with a work pool before submission")
        return f"prefect-pool:{self.work_pool.id}"

    @property
    def sprite_prefix(self) -> str:
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
        self, flow_run: FlowRun, configuration: SpritesJobConfiguration, task_status=None
    ) -> SpritesWorkerResult:
        name = f"{self.sprite_prefix}{flow_run.id.hex}-r{flow_run.run_count}"
        labels = [self.ownership, f"flow-run:{flow_run.id}"]
        async with AsyncSpritesClient(token=configuration.credentials.get_token()) as client:
            try:
                sprite = await self._measure_stage(
                    name,
                    "create",
                    client.create_sprite(
                        name, url_settings=URLSettings(auth="sprite"), labels=labels
                    ),
                )
            except SpriteError as creation_error:
                # The pinned SDK reports HTTP 409 as plain SpriteError, with no
                # status attribute. Resolve uncertain creation by identity;
                # never parse error text or adopt an unowned Sprite.
                try:
                    sprite = await client.get_sprite(name)
                except NotFoundError:
                    raise creation_error from None
                self._check_ownership(sprite, flow_run.id)
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
                provider.install(sprite, config, local_packages=configuration.local_packages),
            )
            if await provider.get_service(sprite) is None:
                await self._measure_stage(name, "service_start", provider.start(sprite))
            if task_status is not None:
                task_status.started(name)
            return SpritesWorkerResult(identifier=name, status_code=0)

    async def _initiate_run(
        self, flow_run: FlowRun, configuration: SpritesJobConfiguration
    ) -> None:
        await self.run(flow_run, configuration)

    def _check_ownership(self, sprite, flow_run_id: UUID | None = None) -> None:
        if self.ownership not in sprite.labels or not sprite.name.startswith(self.sprite_prefix):
            raise InfrastructureNotAvailable("Sprite belongs to a different work pool")
        if flow_run_id and f"flow-run:{flow_run_id}" not in sprite.labels:
            raise InfrastructureNotAvailable("Sprite belongs to a different flow run")

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
                    "Sprites observation failed; retaining infrastructure for next check"
                )
            await anyio.sleep(10)

    async def observe_pool(self, configuration: SpritesJobConfiguration) -> None:
        async with AsyncSpritesClient(token=configuration.credentials.get_token()) as client:
            cursor = None
            while True:
                page = await client.list_sprites(
                    ListOptions(
                        prefix=self.sprite_prefix, continuation_token=cursor, max_results=100
                    )
                )
                semaphore = asyncio.Semaphore(8)

                async def reconcile_one(info, semaphore=semaphore):
                    async with semaphore:
                        try:
                            self._check_ownership(info)
                            sprite = client.sprite(info.name)
                            sprite.labels = info.labels
                            sprite.created_at = info.created_at
                            flow_id = UUID(
                                next(
                                    label.removeprefix("flow-run:")
                                    for label in sprite.labels
                                    if label.startswith("flow-run:")
                                )
                            )
                            await self.reconcile(sprite, flow_id)
                        except NotFoundError:
                            return
                        except Exception:
                            self._logger.exception(
                                "Could not reconcile Sprite %s; will retry", info.name
                            )

                await asyncio.gather(*(reconcile_one(info) for info in page.sprites))
                if not page.has_more:
                    break
                cursor = page.next_continuation_token
                if not cursor:
                    raise RuntimeError("Sprites returned an incomplete pagination cursor")
            await self.reconcile_missing(client)

    async def reconcile_missing(self, client) -> None:
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
                if not pid or not pid.startswith(self.sprite_prefix):
                    continue
                try:
                    await client.get_sprite(pid)
                except NotFoundError:
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
                            Crashed(message=f"Sprite {pid} no longer exists"),
                            flow_run_id=run.id,
                        )
            if len(runs) < 100:
                return
            offset += len(runs)

    async def reconcile(self, sprite, flow_id: UUID) -> None:
        self._check_ownership(sprite, flow_id)
        try:
            flow_run = await self.client.read_flow_run(flow_id)
        except ObjectNotFound:
            self._logger.warning("Sprite %s has no Prefect flow record", sprite.name)
            return
        state = flow_run.state
        if state is None:
            return
        observation = await self._measure_stage(sprite.name, "inspect", provider.inspect(sprite))
        if observation["phase"] == "prepared":
            age = time.time() - sprite.created_at.timestamp()
            # Allow the bounded (300s) environment installation to finish before
            # interpreting a missing service as abandoned submission.
            if age < 600:
                return
            service = await provider.get_service(sprite)
            if service and service.state and service.state.status in {"running", "starting"}:
                return
            observation = {
                "phase": "exited",
                "exit_code": 255,
                "reason": "submission did not start",
                "finished_at": time.time() - 31,
            }
        if observation["phase"] == "running" and not state.is_cancelling():
            service = await provider.get_service(sprite)
            if (
                service
                and service.state
                and service.state.status in {"stopped", "failed"}
                and not service.state.next_restart_at
            ):
                current = await self.client.read_flow_run(flow_id)
                if current.state is None or current.state.is_cancelling():
                    return
                # Restart only the trusted supervisor. Its persisted-running
                # recovery path stops orphaned children and records a crash;
                # it never replays the flow command.
                events = await sprite.start_service(provider.SERVICE, duration=0.1)
                if any(event.type == "error" for event in events):
                    raise InfrastructureNotAvailable("Sprite supervisor recovery failed")
            return
        if observation["phase"] != "exited":
            return
        if self._release_environment:
            await self._release_environment(sprite.name)
        # Provider inspection can take long enough for orchestration to advance
        # to another attempt or pause. Base decisions on a fresh API read.
        flow_run = await self.client.read_flow_run(flow_id)
        state = flow_run.state
        if state is None:
            return
        current_attempt = flow_run.infrastructure_pid == sprite.name
        if flow_run.infrastructure_pid is None:
            expected_name = f"{self.sprite_prefix}{flow_id.hex}-r{flow_run.run_count}"
            current_attempt = sprite.name == expected_name
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
                            f"Sprite execution ended: {observation['reason']} "
                            f"(exit {observation['exit_code']})"
                        )
                    ),
                    flow_run_id=flow_id,
                )
        # Keep diagnostics if artifact delivery fails; a subsequent observation
        # retries cleanup. Artifact keys identify the exact infrastructure attempt.
        await self._measure_stage(
            sprite.name,
            "artifact_delivery",
            self.client.create_artifact(
                ArtifactCreate(
                    key=f"sprite-{sprite.name}",
                    type="table",
                    flow_run_id=flow_id,
                    description="Sprite execution outcome and final diagnostic output",
                    data=[observation],
                )
            ),
        )
        self._logger.info(
            "Sprite cleanup sprite=%s seconds_since_execution=%.6f",
            sprite.name,
            time.time() - observation["finished_at"],
        )
        await self._measure_stage(sprite.name, "delete", sprite.destroy())

    async def kill_infrastructure(
        self,
        infrastructure_pid: str,
        configuration: SpritesJobConfiguration,
        grace_seconds: int = 30,
    ) -> None:
        async with AsyncSpritesClient(token=configuration.credentials.get_token()) as client:
            try:
                sprite = await client.get_sprite(infrastructure_pid)
                self._check_ownership(sprite)
                if await provider.get_service(sprite) is None:
                    # Cancellation can arrive while uv is still bootstrapping.
                    # Destroying the owned Sprite stops that installation too;
                    # it must not later create a service for a cancelled run.
                    if self._release_environment:
                        await self._release_environment(infrastructure_pid)
                    await sprite.destroy()
                    return
                await sprite.stop_service(provider.SERVICE, timeout=grace_seconds)
                service = await provider.get_service(sprite)
                if (
                    service is None
                    or service.state is None
                    or service.state.status not in {"stopped", "failed"}
                ):
                    raise InfrastructureNotAvailable("Sprite service has not stopped yet")
                if self._release_environment:
                    await self._release_environment(infrastructure_pid)
            except NotFoundError as exc:
                raise InfrastructureNotFound(infrastructure_pid) from exc
