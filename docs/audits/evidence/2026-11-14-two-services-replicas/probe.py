# /// script
# requires-python = ">=3.13,<3.15"
# dependencies = ["prefect==3.9.3"]
# ///
import argparse
import asyncio
import collections
import json
import subprocess
from datetime import UTC, datetime, timedelta

from prefect.client.orchestration import PrefectClient
from prefect.client.schemas.actions import DeploymentScheduleCreate
from prefect.client.schemas.schedules import IntervalSchedule
from prefect.events import Event
from prefect.events.actions import DoNothing
from prefect.events.clients import PrefectEventsClient
from prefect.events.schemas.automations import AutomationCore, EventTrigger, Posture

PINGS, GROUPS, BURST = 200, 20, 5


async def total(c, since, name, resource=None):
    f = {
        "occurred": {
            "since": since.isoformat(),
            "until": (datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
        },
        "event": {"name": [name]},
    }
    if resource:
        f["resource"] = {"id": [resource]}
    r = await c._client.post("/events/filter", json={"filter": f, "limit": 1})
    r.raise_for_status()
    return r.json()["total"]


async def main():
    p = argparse.ArgumentParser()
    p.add_argument("--api", required=True)
    p.add_argument("--kill-cmd")
    a = p.parse_args()
    since = datetime.now(UTC) - timedelta(seconds=5)
    out = {}
    async with PrefectClient(api=a.api) as c:

        def auto(name, trig):
            return c.create_automation(
                AutomationCore(name=name, trigger=trig, actions=[DoNothing()])
            )

        each = await auto(
            "each",
            EventTrigger(
                expect={"lab.ping"},
                match={"prefect.resource.id": "lab.ping.*"},
                posture=Posture.Reactive,
                threshold=1,
                within=timedelta(0),
            ),
        )
        burst = await auto(
            "burst",
            EventTrigger(
                expect={"lab.burst"},
                match={"prefect.resource.id": "lab.burst.*"},
                for_each={"prefect.resource.id"},
                posture=Posture.Reactive,
                threshold=BURST,
                within=timedelta(seconds=60),
            ),
        )
        absent = await auto(
            "absent",
            EventTrigger(
                after={"lab.start"},
                expect={"lab.finish"},
                match={"prefect.resource.id": "lab.job.*"},
                for_each={"prefect.resource.id"},
                posture=Posture.Proactive,
                threshold=1,
                within=timedelta(seconds=10),
            ),
        )
        flow_id = await c.create_flow_from_name("lab-flow")
        dep = await c.create_deployment(
            flow_id=flow_id,
            name="lab-dep",
            schedules=[
                DeploymentScheduleCreate(
                    schedule=IntervalSchedule(interval=timedelta(seconds=20)), active=True
                )
            ],
        )
        await asyncio.sleep(40)  # automation cache refreshes every 30s

        events = [("lab.ping", f"lab.ping.{i}") for i in range(PINGS)]
        events += [("lab.burst", f"lab.burst.{g}") for _ in range(BURST) for g in range(GROUPS)]
        events += [("lab.start", f"lab.job.{g}") for g in range(GROUPS)]
        half = len(events) // 2
        async with PrefectEventsClient(api_url=a.api) as ec:
            for i, (name, rid) in enumerate(events):
                if i == half and a.kill_cmd:
                    out["kill"] = subprocess.run(
                        a.kill_cmd, shell=True, capture_output=True, text=True, check=False
                    ).stdout.strip()
                await ec.emit(
                    Event(
                        event=name,
                        resource={"prefect.resource.id": rid},
                        occurred=datetime.now(UTC),
                    )
                )
                await asyncio.sleep(0.07)
        await asyncio.sleep(60)

        out["emitted"] = dict(collections.Counter(n for n, _ in events))
        out["persisted"] = {
            n: await total(c, since, n) for n in ("lab.ping", "lab.burst", "lab.start")
        }
        for label, aid, want in (
            ("each", each, PINGS),
            ("burst", burst, GROUPS),
            ("absent", absent, GROUPS),
        ):
            rid = f"prefect.automation.{aid}"
            out[label] = {
                "want": want,
                "triggered": await total(c, since, "prefect.automation.triggered", rid),
                "executed": await total(c, since, "prefect.automation.action.executed", rid),
                "failed": await total(c, since, "prefect.automation.action.failed", rid),
            }
        runs = await c.read_flow_runs(limit=200)
        times = collections.Counter(r.expected_start_time for r in runs if r.deployment_id == dep)
        out["scheduled_runs"] = {
            "runs": sum(times.values()),
            "distinct_times": len(times),
            "duplicates": sum(v - 1 for v in times.values()),
        }
    print(json.dumps(out))


asyncio.run(main())
