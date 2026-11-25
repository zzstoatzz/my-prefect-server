"""Schedule a one-night override of the home lighting wind-down."""

import argparse
from datetime import UTC, datetime

from mps.wind_down import HOME_TIMEZONE, WindDown, override_name
from prefect.client.orchestration import get_client
from prefect.states import Scheduled
from prefect.variables import Variable


def parse_start(value: str) -> datetime:
    start = datetime.fromisoformat(value)
    if start.tzinfo is None:
        start = start.replace(tzinfo=HOME_TIMEZONE)
        if start.utcoffset() != start.replace(fold=1).utcoffset():
            raise ValueError("Give an explicit UTC offset at a daylight-saving clock change")
    return start.astimezone(UTC)


def schedule_override(start: datetime, minutes: int) -> None:
    if start <= datetime.now(UTC):
        raise ValueError("Choose a start time in the future")
    plan = WindDown(minutes=minutes, override=True)
    local_start = start.astimezone(HOME_TIMEZONE)
    parameters = {"wind_down": plan.model_dump()}
    with get_client(sync_client=True) as client:
        deployment = client.read_deployment_by_name("lighting/home")
        if deployment.paused or "wind_down" not in deployment.parameter_openapi_schema.get(
            "properties", {}
        ):
            raise ValueError("The lighting/home deployment must be active and support wind_down")
        run = client.create_flow_run_from_deployment(
            deployment.id,
            parameters=parameters,
            state=Scheduled(scheduled_time=start),
            name=f"wind-down-override-{local_start:%Y-%m-%d-%H%M}",
            idempotency_key=f"lighting-wind-down:{start.isoformat()}:{minutes}",
        )
    Variable.set(override_name(local_start.date()), str(run.id), overwrite=True)
    print(f"Scheduled {local_start.isoformat()} for {minutes} minutes (run {run.id})")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("start", help="ISO date and time, defaulting to America/Chicago")
    parser.add_argument("--minutes", type=int, default=20)
    args = parser.parse_args()
    try:
        schedule_override(parse_start(args.start), args.minutes)
    except ValueError as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
