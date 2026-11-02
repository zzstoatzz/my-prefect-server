"""Owned, short-lived Hetzner infrastructure for the cloud index builder."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from uuid import UUID

import httpx

from mps.builder_billing import record

OWNER = "my-prefect-server"
PURPOSE = "ephemeral-builder"
CAPACITY_CODES = frozenset({"resource_unavailable", "placement_error"})


class HCloudError(RuntimeError):
    def __init__(self, message: str, status_code: int, code: str):
        super().__init__(message)
        self.status_code = status_code
        self.code = code


def placements(
    server_type: str,
    location: str,
    fallback_server_types: tuple[str, ...] | list[str] = (),
    fallback_locations: tuple[str, ...] | list[str] = (),
) -> list[tuple[str, str]]:
    types = list(dict.fromkeys([server_type, *fallback_server_types]))
    locations = list(dict.fromkeys([location, *fallback_locations]))
    return [(kind, where) for kind in types for where in locations]


def labels_for(run_id: UUID, attempt: int, timeout: int) -> dict[str, str]:
    if not 60 <= timeout <= 46800 or attempt < 0:
        raise ValueError("Invalid builder lifetime or attempt")
    return {
        "owner": OWNER,
        "purpose": PURPOSE,
        "flow-run": run_id.hex,
        "attempt": str(attempt),
        "expires": str(int(time.time()) + timeout + 900),
    }


def resource_name(run_id: UUID, attempt: int) -> str:
    return f"prefect-build-{run_id.hex}-r{attempt}"


class HCloud:
    def __init__(self, token: str, billing_directory: Path | None = None):
        self.billing_directory = billing_directory
        self.pricing = None
        self.http = httpx.AsyncClient(
            base_url="https://api.hetzner.cloud/v1/",
            headers={"Authorization": "Bearer " + token},
            timeout=30,
        )

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        await self.http.aclose()

    async def request(self, method: str, path: str, **kwargs):
        response = await self.http.request(method, path, **kwargs)
        if response.status_code == 404:
            return None
        if response.is_error:
            code = response.json().get("error", {}).get("code", "unknown")
            raise HCloudError(
                f"Hetzner {method} {path}: HTTP {response.status_code} ({code})",
                response.status_code,
                code,
            )
        return response.json() if response.content else {}

    async def owned_servers(self) -> list[dict]:
        result = []
        page = 1
        while True:
            data = await self.request(
                "GET",
                "servers",
                params={
                    "label_selector": f"owner={OWNER},purpose={PURPOSE}",
                    "page": page,
                    "per_page": 50,
                },
            )
            assert data is not None
            result.extend(data["servers"])
            if not data["meta"]["pagination"].get("next_page"):
                return result
            page += 1

    @staticmethod
    def check_owned(server: dict, run_id: UUID | None = None) -> None:
        labels = server.get("labels", {})
        if labels.get("owner") != OWNER or labels.get("purpose") != PURPOSE:
            raise ValueError("Refusing to mutate an unowned server")
        identity = UUID(hex=labels["flow-run"])
        attempt = int(labels["attempt"])
        if server["name"] != resource_name(identity, attempt):
            raise ValueError("Server name and ownership labels disagree")
        if run_id is not None and identity != run_id:
            raise ValueError("Server belongs to another flow run")
        int(labels["expires"])

    async def create(
        self,
        run_id: UUID,
        attempt: int,
        public_key: str,
        user_data: str,
        timeout: int = 43200,
        server_type: str = "cpx32",
        location: str = "fsn1",
        pool: str = "canary",
        fallback_server_types: tuple[str, ...] | list[str] = (),
        fallback_locations: tuple[str, ...] | list[str] = (),
        capacity_wait_seconds: float = 0,
        capacity_retry_seconds: float = 300,
    ) -> dict:
        name = resource_name(run_id, attempt)
        labels = {
            **labels_for(run_id, attempt, timeout + int(capacity_wait_seconds)),
            "pool": pool,
            "cost-project": "typeahead",
        }
        existing = [s for s in await self.owned_servers() if s["name"] == name]
        if existing:
            if len(existing) != 1:
                raise RuntimeError("Multiple servers claim the same execution")
            self.check_owned(existing[0], run_id)
            await self.configure_ip(existing[0])
            return existing[0]
        key_name = name + "-key"
        keys = await self.request("GET", "ssh_keys", params={"name": key_name})
        assert keys is not None
        found = [k for k in keys["ssh_keys"] if k["name"] == key_name]
        if found:
            key = found[0]
            if key["public_key"].split()[:2] != public_key.split()[:2] or {
                k: v for k, v in key.get("labels", {}).items() if k != "expires"
            } != {k: v for k, v in labels.items() if k != "expires"}:
                raise ValueError("SSH key ownership or bytes disagree")
            labels = key["labels"]
        else:
            result = await self.request(
                "POST",
                "ssh_keys",
                json={"name": key_name, "public_key": public_key, "labels": labels},
            )
            assert result is not None
            key = result["ssh_key"]
        options = placements(server_type, location, fallback_server_types, fallback_locations)
        deadline = time.monotonic() + capacity_wait_seconds
        index = 0
        while True:
            kind, where = options[index % len(options)]
            try:
                result = await self.request(
                    "POST",
                    "servers",
                    json={
                        "name": name,
                        "server_type": kind,
                        "location": where,
                        "image": "ubuntu-24.04",
                        "ssh_keys": [key["id"]],
                        "labels": labels,
                        "user_data": user_data,
                        "public_net": {"enable_ipv4": True, "enable_ipv6": False},
                    },
                )
                assert result is not None
                server = result["server"]
                break
            except (httpx.TransportError, RuntimeError) as error:
                resolved = [s for s in await self.owned_servers() if s["name"] == name]
                if not resolved and isinstance(error, HCloudError) and error.code in CAPACITY_CODES:
                    index += 1
                    if index % len(options):
                        continue
                    remaining = deadline - time.monotonic()
                    if remaining > 0:
                        await asyncio.sleep(min(capacity_retry_seconds, remaining))
                        continue
                if not resolved:
                    await self.request("DELETE", f"ssh_keys/{key['id']}")
                    raise
                if len(resolved) != 1:
                    raise RuntimeError("Ambiguous server creation identity") from None
                server = resolved[0]
                break
        self.check_owned(server, run_id)
        try:
            await self.bill(server, "created")
            await self.configure_ip(server)
        except BaseException:
            await self.delete(server["id"], run_id, receipt=server)
            raise
        return server

    async def bill(self, server: dict, phase: str) -> None:
        if self.billing_directory is None:
            return
        if self.pricing is None:
            result = await self.request("GET", "pricing")
            self.pricing = result["pricing"]
        record(self.billing_directory, server, self.pricing, phase)

    async def configure_ip(self, server: dict) -> None:
        self.check_owned(server)
        ipv4 = server["public_net"].get("ipv4")
        if ipv4 is None:
            raise RuntimeError("Builder has no public SSH address")
        result = await self.request("GET", f"primary_ips/{ipv4['id']}")
        if result is None or result["primary_ip"].get("assignee_id") != server["id"]:
            raise ValueError("Builder IP ownership disagrees")
        await self.request(
            "PUT",
            f"primary_ips/{ipv4['id']}",
            json={
                "auto_delete": True,
                "labels": server["labels"],
                "name": server["name"] + "-ipv4",
            },
        )

    async def delete(self, server_id: int, run_id: UUID, receipt: dict | None = None) -> None:
        result = await self.request("GET", f"servers/{server_id}")
        if result is None:
            if receipt is None:
                return
            server = receipt
            if server["id"] != server_id:
                raise ValueError("Cleanup receipt belongs to another server")
        else:
            server = result["server"]
        self.check_owned(server, run_id)
        ipv4 = server["public_net"].get("ipv4")
        ip_id = ipv4.get("id") if ipv4 else None
        # Preserve lifecycle and prices before inventory disappears. A telemetry
        # failure must never keep a billable VM alive.
        billing_error = None
        try:
            await self.bill(server, "created")
        except Exception as error:
            billing_error = error
        if result is not None:
            await self.request("DELETE", f"servers/{server_id}")
        for _ in range(60):
            if await self.request("GET", f"servers/{server_id}") is None:
                break
            await asyncio.sleep(2)
        else:
            raise RuntimeError("Server deletion still pending; retain cleanup receipt")
        keys = await self.request("GET", "ssh_keys", params={"name": server["name"] + "-key"})
        assert keys is not None
        for key in keys["ssh_keys"]:
            if key["name"] == server["name"] + "-key" and key.get("labels") == server["labels"]:
                await self.request("DELETE", f"ssh_keys/{key['id']}")
        if ip_id is not None:
            for _ in range(30):
                ip = await self.request("GET", f"primary_ips/{ip_id}")
                if ip is None:
                    break
                assignee = ip["primary_ip"].get("assignee_id")
                if assignee is None:
                    await self.request("DELETE", f"primary_ips/{ip_id}")
                    break
                if assignee != server_id:
                    raise RuntimeError("Deleted builder IP has been reassigned; refusing cleanup")
                await asyncio.sleep(2)
            else:
                raise RuntimeError("Builder IP cleanup is still pending; retain cleanup receipt")

        try:
            await self.bill(server, "deleted")
        except Exception as error:
            billing_error = error
        if billing_error is not None:
            raise RuntimeError(
                "Builder cleaned up; billing receipt unavailable, retaining local resource receipt for retry"
            ) from billing_error
