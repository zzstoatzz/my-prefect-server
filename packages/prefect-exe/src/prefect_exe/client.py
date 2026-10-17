"""exe.dev control plane and VM access over SSH."""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import shlex
import tempfile
from dataclasses import dataclass
from pathlib import Path

CONTROL_HOST = "exe.dev"

# exe.dev's SSH front end answers for the control host and every VM with one key.
# Fingerprint SHA256:JJOP/lwiBGOMilfONPWZCXUrfK154cnJFXcqlsi6lPo, published at
# https://exe.dev/docs/faq. Pinning it keeps per-run VM names out of the worker
# user's known_hosts and refuses an unexpected host.
HOST_KEY = (
    "ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQDEKtEcRW8OBtro5B/MG+EaisD+ZVwwHFa5m7M8wFwBlMmPJJssY+"
    "1aGBRW3b9InAeCnTU2Kt7gazqbg/9od1KnK6x5piQNVQZ4C/lrjsC2ScBrOydnw9ry9G2+voFCAk+dQGabIrIT6gqq"
    "DJNOqxgFiG/lA3Xx6KwpfwI2BH5f3ab2fHCR2BGAC5jlB2RJXPgly80hMxYEHqexhJxYRwC+deeLrQSG795we9rSzP"
    "mdz58t9+9jLTKkyyqWKe/hmBvty1AYrEmRsefu6/TUrIGi/UWJfa+RBIQtFgWqN6xT1F6rRwELeVOfwwr5tZbsmgWY"
    "5frZU3EOtVWcF7Ve3gfL"
)

# ssh reserves 255 for its own failures, so it never describes the remote command.
TRANSPORT_FAILURE = 255


class ExeError(Exception):
    def __init__(self, returncode: int, output: str):
        super().__init__(f"exe.dev command failed ({returncode}): {output.strip()[-500:]}")
        self.returncode = returncode
        self.output = output


class NotFound(ExeError):
    def __init__(self, name: str):
        super().__init__(0, f"VM {name} not found")


@dataclass(frozen=True)
class VM:
    name: str
    host: str
    tags: tuple[str, ...]
    created_at: dt.datetime

    @classmethod
    def from_listing(cls, record: dict) -> VM:
        return cls(
            name=record["vm_name"],
            host=record["ssh_dest"],
            tags=tuple(record.get("tags") or ()),
            created_at=dt.datetime.fromisoformat(record["created_at"]),
        )


class ExeClient:
    def __init__(self, identity_file: Path | None = None):
        self._identity_file = identity_file
        self._known_hosts = None

    async def __aenter__(self) -> ExeClient:
        self._known_hosts = tempfile.NamedTemporaryFile("w", prefix="exe-known-hosts-")  # noqa: SIM115
        self._known_hosts.write(f"{CONTROL_HOST},*.exe.xyz {HOST_KEY}\n")
        self._known_hosts.flush()
        return self

    async def __aexit__(self, *exc_info) -> None:
        self._known_hosts.close()
        self._known_hosts = None

    async def _ssh(
        self, host: str, command: str, *, stdin: bytes | None = None, timeout: float = 60
    ) -> str:
        if self._known_hosts is None:
            raise RuntimeError("ExeClient must be entered before use")
        argv = [
            "ssh",
            "-o", "BatchMode=yes",
            "-o", "StrictHostKeyChecking=yes",
            "-o", f"UserKnownHostsFile={self._known_hosts.name}",
            "-o", "GlobalKnownHostsFile=/dev/null",
            "-o", "ConnectTimeout=10",
            "-o", "LogLevel=ERROR",
        ]  # fmt: skip
        if self._identity_file is not None:
            argv += ["-o", "IdentitiesOnly=yes", "-i", str(self._identity_file)]
        process = await asyncio.create_subprocess_exec(
            *argv,
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
        if process.returncode != 0:
            raise ExeError(process.returncode, text)
        return text

    async def list(self, prefix: str = "") -> list[VM]:
        listing = json.loads(await self._ssh(CONTROL_HOST, "ls --json"))
        return [
            VM.from_listing(record)
            for record in listing["vms"]
            if record["vm_name"].startswith(prefix)
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
        tags: list[str],
        image: str | None = None,
        integrations: tuple[str, ...] = (),
    ) -> VM:
        command = f"new --name={name} --tag={','.join(tags)} --no-email --json"
        if image:
            command += f" --image={shlex.quote(image)}"
        for integration in integrations:
            command += f" --integration={shlex.quote(integration)}"
        await self._ssh(CONTROL_HOST, command, timeout=120)
        return await self.get(name)

    async def destroy(self, name: str) -> None:
        # `rm` exits 0 for a VM that is already gone, so confirm by listing.
        await self._ssh(CONTROL_HOST, f"rm {name}")
        try:
            await self.get(name)
        except NotFound:
            return
        raise ExeError(0, f"VM {name} still exists after deletion")

    async def detach(self, name: str, integration: str) -> None:
        await self._ssh(
            CONTROL_HOST, f"integrations detach {shlex.quote(integration)} vm:{shlex.quote(name)}"
        )

    async def run(
        self, vm: VM, command: str, *, stdin: bytes | None = None, timeout: float = 60
    ) -> str:
        return await self._ssh(vm.host, command, stdin=stdin, timeout=timeout)
