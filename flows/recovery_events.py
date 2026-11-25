"""Publish verified exhaustion of Cellar's existing Fly restart policy."""

from datetime import UTC, datetime
from uuid import NAMESPACE_URL, uuid5

from mps.alerts.recovery import EVENT_NAME, RESOURCE_ID, RecoveryFailed
from prefect.events import emit_event

from flows.evergreen_checks import Finding
from flows.fleet_events import run_link
from flows.fly_observations import CELLAR_APP, is_observation_list


def publish_exhausted_recovery(evergreen: object, fly: object) -> None:
    if not isinstance(evergreen, list) or not is_observation_list(fly):
        return
    cellar = [machine for machine in fly if machine.app == CELLAR_APP]
    if len(cellar) != 1:
        return
    machine = cellar[0]
    event = machine.exhausted_restart
    if event is None or event.request.restart_count is None or event.request.exit_event is None:
        return
    failed_at = datetime.fromtimestamp(event.timestamp / 1000, UTC)
    if not any(
        isinstance(finding, Finding)
        and finding.project == "pollz"
        and finding.name == "pollz backend"
        and not finding.healthy
        and finding.checked_at >= failed_at
        for finding in evergreen
    ):
        return
    details = event.request.exit_event
    evidence = RecoveryFailed(
        machine=machine.machine,
        release=machine.machine_version,
        attempts=event.request.restart_count,
        exit_code=details.exit_code,
        oom_killed=details.oom_killed,
        failed_at=failed_at,
        sweep_url=run_link() or None,
    )
    emit_event(
        event=EVENT_NAME,
        resource={"prefect.resource.id": RESOURCE_ID},
        id=uuid5(NAMESPACE_URL, f"{RESOURCE_ID}/{machine.machine}/{event.id}"),
        payload=evidence.model_dump(mode="json"),
    )
