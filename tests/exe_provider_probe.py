"""Explicit live probe: real exe.dev VMs through the real client and provider.

Creates two VMs on the account behind the default SSH identity and deletes them.
"""

import argparse
import asyncio
import sys
import time
import uuid
from pathlib import Path

from prefect_exe import provider
from prefect_exe.client import ExeClient, NotFound

DETACHED = (
    "import subprocess, sys; "
    "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(300)'], "
    "start_new_session=True); print('exe-provider-ok')"
)


def config(argv: list[str], *, image: str | None) -> dict:
    return {
        "flow_run_id": "probe",
        "environment_mode": "image" if image else "bootstrap",
        "argv": argv,
        "env": {"PROBE_SETTING": "explicit"},
        "cwd": "/home/exedev",
        "timeout_seconds": 120,
        "requirements": [],
        "runtime_bin": f"{provider.DIRECTORY}/venv/bin",
        "cgroup": "/sys/fs/cgroup/prefect-flow",
    }


async def wait_for(client, vm, phase: str, seconds: float = 60) -> dict:
    deadline = time.monotonic() + seconds
    while True:
        state = await provider.inspect(client, vm)
        if state["phase"] == phase:
            return state
        assert time.monotonic() < deadline, state
        await asyncio.sleep(1)


async def timed(label: str, operation):
    started = time.monotonic()
    result = await operation
    print(f"{label}: {time.monotonic() - started:.1f}s", flush=True)
    return result


async def main(image: str | None, identity_file: Path | None):
    suffix = uuid.uuid4().hex[:8]
    names = [f"prefect-probe-{suffix}-{kind}" for kind in ("exit", "stop")]
    async with ExeClient(identity_file) as client:
        try:
            vm = await timed("create", client.create(names[0], tags=["prefect-probe"], image=image))
            assert vm.tags == ("prefect-probe",) and vm.created_at.tzinfo is not None
            assert await provider.service_status(client, vm) is None
            assert await provider.inspect(client, vm) == {"phase": "prepared"}
            await timed(
                "bootstrap",
                provider.install(client, vm, config(["python", "-c", DETACHED], image=image)),
            )
            await provider.install(client, vm, config(["python", "-c", DETACHED], image=image))
            await provider.start(client, vm)
            state = await timed("exit observed", wait_for(client, vm, "exited"))
            assert state["exit_code"] == 0 and state["reason"] == "process exited", state
            assert "exe-provider-ok" in state["log_tail"], state
            assert await provider.service_status(client, vm) == "stopped"
            cgroup = await client.run(vm, "sudo -n cat /sys/fs/cgroup/prefect-flow/cgroup.events")
            assert "populated 0" in cgroup, cgroup
            hidden = await client.run(vm, f"ls {provider.DIRECTORY} 2>&1 || true")
            assert "Permission denied" in hidden, hidden
            await provider.restart(client, vm)
            await asyncio.sleep(2)
            assert (await provider.inspect(client, vm))["finished_at"] == state["finished_at"]
            print("exit: outcome kept; detached child stopped; restart did not replay", flush=True)

            vm = await client.create(names[1], tags=["prefect-probe"], image=image)
            await provider.install(
                client, vm, config(["python", "-c", "import time; time.sleep(300)"], image=image)
            )
            await provider.start(client, vm)
            await wait_for(client, vm, "running")
            assert await provider.service_status(client, vm) == "running"
            await timed("stop", provider.stop(client, vm, 30))
            state = await provider.inspect(client, vm)
            assert state["exit_code"] == 143 and state["reason"] == "execution cancelled", state
            assert await provider.service_status(client, vm) in {"stopped", "failed"}
            print("stop: supervisor cancelled the run and recorded it", flush=True)
        finally:
            for name in names:
                await client.destroy(name)
        for name in names:
            try:
                await client.get(name)
            except NotFound:
                continue
            raise AssertionError(f"{name} still exists")
        print("cleanup: both VMs deleted", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image")
    parser.add_argument("--identity-file", type=Path)
    args = parser.parse_args()
    sys.exit(asyncio.run(main(args.image, args.identity_file)))
