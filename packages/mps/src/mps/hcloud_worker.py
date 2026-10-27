"""Run each builder execution on an owned disposable cloud VM."""

from __future__ import annotations

import argparse
import asyncio
import builtins
import datetime as dt
import json
import os
import re
import shlex
import time
from pathlib import Path
from uuid import UUID

import httpx
from prefect.client.schemas.objects import FlowRun
from prefect_exe import ExeWorker
from prefect_exe.client import VM, ExeClient, ExeError, NotFound
from prefect_exe.worker import ExeJobConfiguration
from pydantic import Field

from mps.exe_tailnet import INSTALL as TAILNET_INSTALL
from mps.hcloud_builder import HCloud, resource_name

BOOTSTRAP = """#!/bin/sh
set -eu
export DEBIAN_FRONTEND=noninteractive
export HOME=/root GOPATH=/root/go GOCACHE=/root/.cache/go-build
apt-get update -qq
apt-get install -y -qq --no-install-recommends python3 ca-certificates curl git rclone golang-go xz-utils sudo
curl -LsSf https://astral.sh/uv/0.12.23/install.sh | env UV_UNMANAGED_INSTALL=/usr/local/bin sh
GOTOOLCHAIN=go1.26.0 GOBIN=/usr/local/bin go install github.com/tailscale/ts-plug/cmd/ts-unplug@0b0b083dae1aad0ad55c62ba28d3402f421a9aab
mkdir -p /root/.local/opt
curl -LsSf https://ziglang.org/download/0.17.0/zig-x86_64-linux-0.17.0.tar.xz -o /tmp/zig.tar.xz
echo '1cbe9df9f27e6b78d14ccbca43b6703a404ef79ef1c463de901d7f088d4e2026  /tmp/zig.tar.xz' | sha256sum --check --status
tar -xJf /tmp/zig.tar.xz -C /root/.local/opt
rm /tmp/zig.tar.xz
mkdir -p /var/lib/prefect-builder /var/lib/typeahead-indexer/work /var/lib/typeahead-indexer/active /var/lib/typeahead-indexer/builds/archive
touch /var/lib/prefect-builder/bootstrap-ready
"""


class BuilderSSH(ExeClient):
    def __init__(self, key: Path, known_hosts: Path):
        super().__init__(key)
        self.hosts = known_hosts

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None

    async def _ssh(
        self, host: str, command: str, *, stdin: bytes | None = None, timeout: float = 60
    ) -> str:
        process = await asyncio.create_subprocess_exec(
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            "IdentitiesOnly=yes",
            "-o",
            "ConnectTimeout=8",
            "-o",
            "StrictHostKeyChecking=accept-new",
            "-o",
            f"UserKnownHostsFile={self.hosts}",
            "-i",
            str(self._identity_file),
            host,
            command,
            stdin=asyncio.subprocess.PIPE if stdin is not None else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            output, _ = await asyncio.wait_for(process.communicate(stdin), timeout)
        except BaseException:
            if process.returncode is None:
                process.kill()
                await process.wait()
            raise
        text = output.decode(errors="replace")
        if process.returncode:
            raise ExeError(process.returncode, text)
        return text


class BuilderConfiguration(ExeJobConfiguration):
    server_type: str = "cpx32"
    location: str = "fsn1"
    timeout_seconds: int = Field(default=43200, ge=60, le=43200)
    requirements: list[str] = Field(default_factory=lambda: ["prefect==3.8.2"])
    working_dir: str = "/var/lib/typeahead-indexer"


async def enrollment_key(config: dict, name: str) -> str:
    async with httpx.AsyncClient(timeout=30) as api:
        r = await api.post(
            "https://api.tailscale.com/api/v2/oauth/token",
            data={
                "grant_type": "client_credentials",
                "client_id": config["client_id"],
                "client_secret": config["client_secret"],
            },
        )
        r.raise_for_status()
        token = r.json()["access_token"]
        r = await api.post(
            "https://api.tailscale.com/api/v2/tailnet/-/keys",
            headers={"Authorization": "Bearer " + token},
            json={
                "expirySeconds": 600,
                "description": name,
                "capabilities": {
                    "devices": {
                        "create": {
                            "reusable": False,
                            "ephemeral": True,
                            "preauthorized": True,
                            "tags": ["tag:gardener-vm"],
                        }
                    }
                },
            },
        )
        r.raise_for_status()
        return r.json()["key"]


class BuilderClient(BuilderSSH):
    def __init__(self, root: Path, ownership: str, config: BuilderConfiguration):
        super().__init__(root / "unused-key", root / "unused-hosts")
        self.root = root
        self.ownership = ownership
        self.configuration = config
        self.hcloud = HCloud(os.environ["HCLOUD_TOKEN"], billing_directory=root / "billing")

    async def __aexit__(self, *_):
        await self.hcloud.__aexit__()

    def vm(self, server: dict) -> VM:
        self.hcloud.check_owned(server)
        if server["labels"].get("pool") != self.ownership:
            raise ValueError("Server belongs to a different work pool")
        return VM(
            server["name"],
            "root@" + server["public_net"]["ipv4"]["ip"],
            (self.ownership, "flow-run-" + server["labels"]["flow-run"]),
            dt.datetime.fromisoformat(server["created"]),
        )

    async def list(self, prefix: str = "") -> builtins.list[VM]:
        servers = await self.hcloud.owned_servers()
        names = {s["name"] for s in servers}
        for receipt in self.root.glob("prefect-build-*/resource.json"):
            server = json.loads(receipt.read_text())["server"]
            self.vm(server)
            if server["name"] not in names:
                await self.destroy(server["name"])
        return [
            self.vm(s)
            for s in servers
            if s["name"].startswith(prefix) and s["labels"].get("pool") == self.ownership
        ]

    async def get(self, name: str) -> VM:
        for vm in await self.list(name):
            if vm.name == name:
                return vm
        raise NotFound(name)

    async def create(
        self,
        name: str,
        *,
        tags: builtins.list[str],
        image: str | None = None,
        integrations: tuple[str, ...] = (),
    ) -> VM:
        if image or integrations or self.ownership not in tags:
            raise ValueError("Invalid cloud builder provisioning request")
        identity = UUID(
            hex=next(t.removeprefix("flow-run-") for t in tags if t.startswith("flow-run-"))
        )
        attempt = int(name.rsplit("-r", 1)[1])
        if name != resource_name(identity, attempt):
            raise ValueError("Builder name and run identity disagree")
        root = self.root / name
        root.mkdir(parents=True, mode=0o700, exist_ok=True)
        key = root / "key"
        if not key.exists():
            process = await asyncio.create_subprocess_exec(
                "ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)
            )
            if await process.wait():
                raise RuntimeError("Could not generate attempt SSH identity")
        config = self.configuration
        server = await self.hcloud.create(
            identity,
            attempt,
            key.with_suffix(".pub").read_text(),
            BOOTSTRAP,
            config.timeout_seconds,
            config.server_type,
            config.location,
            pool=self.ownership,
        )
        (root / "resource.json").write_text(
            json.dumps(
                {
                    "server": server,
                }
            )
        )
        vm = self.vm(server)
        deadline = time.monotonic() + 900
        while True:
            try:
                await self.run(vm, "test -f /var/lib/prefect-builder/bootstrap-ready", timeout=12)
                return vm
            except (ExeError, TimeoutError):
                if time.monotonic() >= deadline:
                    raise RuntimeError("Cloud bootstrap exceeded fifteen minutes") from None
                await asyncio.sleep(10)

    async def run(
        self, vm: VM, command: str, *, stdin: bytes | None = None, timeout: float = 60
    ) -> str:
        root = self.root / vm.name
        async with BuilderSSH(root / "key", root / "known_hosts") as ssh:
            return await ssh.run(vm, command, stdin=stdin, timeout=timeout)

    async def destroy(self, name: str) -> None:
        if not re.fullmatch(r"prefect-build-[0-9a-f]{32}-r(?:0|[1-9][0-9]*)", name):
            raise ValueError("Invalid builder resource name")
        root = self.root / name
        receipt = root / "resource.json"
        if receipt.exists():
            server = json.loads(receipt.read_text())["server"]
            if server["name"] != name:
                raise ValueError("Cleanup receipt name disagrees")
            self.vm(server)
            await self.hcloud.delete(
                server["id"], UUID(hex=server["labels"]["flow-run"]), receipt=server
            )
        for server in await self.hcloud.owned_servers():
            if server["name"] == name:
                self.vm(server)
                await self.hcloud.delete(server["id"], UUID(hex=server["labels"]["flow-run"]))
        if root.exists():
            for path in root.iterdir():
                path.unlink()
            root.rmdir()


class HCloudWorker(ExeWorker):
    type = "hcloud-builder"
    job_configuration = BuilderConfiguration
    bootstrap_grace_seconds = 1200
    _description = "One owned disposable cloud VM per builder execution."
    _display_name = "Ephemeral cloud builder"

    def __init__(self, *args, state_directory: Path, tailnet_config: Path, **kwargs):
        self.directory = state_directory
        self.tailnet_config = tailnet_config
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        super().__init__(
            *args,
            run_environment=self.environment,
            observations_path=self.directory / "observations",
            **kwargs,
        )

    @property
    def vm_prefix(self) -> str:
        return "prefect-build-"

    def provider_client(self, configuration: ExeJobConfiguration) -> ExeClient:
        if not isinstance(configuration, BuilderConfiguration):
            raise ValueError("Expected cloud builder configuration")
        return BuilderClient(self.directory, self.ownership, configuration)

    async def environment(self, name: str, timeout: int, flow_run: FlowRun) -> dict[str, str]:
        config = json.loads(self.tailnet_config.read_text())
        key = await enrollment_key(config, name)
        async with BuilderClient(self.directory, self.ownership, BuilderConfiguration()) as client:
            vm = await client.get(name)
            await client.run(
                vm,
                "python3 -c " + shlex.quote(TAILNET_INSTALL),
                stdin=json.dumps(
                    {"key": key, "name": name, "target": config["target"], "timeout": timeout}
                ).encode(),
                timeout=150,
            )
        return {
            "PREFECT_API_URL": "http://localhost:18080/api",
            "HOME": "/root",
            "XDG_CACHE_HOME": "/root/.cache",
            "ZIG_GLOBAL_CACHE_DIR": "/root/.cache/zig",
        }


if __name__ == "__main__":
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pool", default="typeahead-ephemeral")
    parser.add_argument("--state-directory", type=Path, required=True)
    parser.add_argument("--tailnet-config", type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(
        HCloudWorker(
            work_pool_name=args.pool,
            name="hcloud-builder",
            limit=1,
            state_directory=args.state_directory,
            tailnet_config=args.tailnet_config,
        ).start()
    )
