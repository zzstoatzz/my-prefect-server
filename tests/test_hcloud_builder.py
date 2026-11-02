import asyncio
import json
import time
from uuid import uuid4

import httpx
import pytest
from mps.hcloud_builder import HCloud, HCloudError, placements


def test_placements_try_every_location_before_another_server_type():
    assert placements("cpx32", "hel1", ["cx43", "cpx32"], ["fsn1", "hel1", "nbg1"]) == [
        ("cpx32", "hel1"),
        ("cpx32", "fsn1"),
        ("cpx32", "nbg1"),
        ("cx43", "hel1"),
        ("cx43", "fsn1"),
        ("cx43", "nbg1"),
    ]


class FakeHetzner:
    def __init__(self, rejected, code="resource_unavailable", status=412, recover_after=None):
        self.rejected = rejected
        self.recover_after = recover_after
        self.code = code
        self.status = status
        self.attempts = []
        self.servers = []
        self.keys = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/v1/")
        if request.method == "GET" and path == "servers":
            return httpx.Response(
                200, json={"servers": self.servers, "meta": {"pagination": {"next_page": None}}}
            )
        if request.method == "GET" and path == "ssh_keys":
            return httpx.Response(200, json={"ssh_keys": self.keys})
        if request.method == "POST" and path == "ssh_keys":
            key = {**json.loads(request.content), "id": 7}
            self.keys.append(key)
            return httpx.Response(201, json={"ssh_key": key})
        if request.method == "DELETE" and path == "ssh_keys/7":
            self.keys.clear()
            return httpx.Response(204)
        if request.method == "POST" and path == "servers":
            body = json.loads(request.content)
            placement = (body["server_type"], body["location"])
            self.attempts.append(placement)
            if placement in self.rejected and (
                self.recover_after is None or len(self.attempts) <= self.recover_after
            ):
                return httpx.Response(self.status, json={"error": {"code": self.code}})
            server = {
                "id": 11,
                "name": body["name"],
                "labels": body["labels"],
                "server_type": {"name": body["server_type"]},
                "location": {"name": body["location"]},
                "public_net": {"ipv4": {"id": 13, "ip": "192.0.2.1"}},
            }
            self.servers.append(server)
            return httpx.Response(201, json={"server": server})
        if request.method == "GET" and path == "primary_ips/13":
            return httpx.Response(200, json={"primary_ip": {"assignee_id": 11}})
        if request.method == "PUT" and path == "primary_ips/13":
            return httpx.Response(200, json={})
        raise AssertionError(f"Unexpected Hetzner call {request.method} {path}")


def create(fake, **kwargs):
    async def run():
        async with HCloud("token") as api:
            await api.http.aclose()
            api.http = httpx.AsyncClient(
                base_url="https://api.hetzner.cloud/v1/", transport=httpx.MockTransport(fake)
            )
            return await api.create(
                uuid4(), 0, "ssh-ed25519 AAAA test", "#cloud-config", 600, **kwargs
            )

    return asyncio.run(run())


def test_capacity_rejection_falls_back_to_another_location_then_type():
    fake = FakeHetzner(rejected={("cpx32", "hel1"), ("cpx32", "fsn1")})
    server = create(
        fake,
        server_type="cpx32",
        location="hel1",
        fallback_server_types=["cx43"],
        fallback_locations=["fsn1"],
    )
    assert fake.attempts == [("cpx32", "hel1"), ("cpx32", "fsn1"), ("cx43", "hel1")]
    assert server["location"]["name"] == "hel1" and server["server_type"]["name"] == "cx43"
    assert len(fake.keys) == 1


def test_exhausted_capacity_raises_and_removes_the_attempt_key():
    fake = FakeHetzner(rejected={("cpx32", "hel1"), ("cpx32", "fsn1")})
    with pytest.raises(HCloudError, match="resource_unavailable"):
        create(fake, server_type="cpx32", location="hel1", fallback_locations=["fsn1"])
    assert fake.attempts == [("cpx32", "hel1"), ("cpx32", "fsn1")]
    assert fake.keys == []


def test_non_capacity_rejection_does_not_try_other_placements():
    fake = FakeHetzner(rejected={("cpx32", "hel1")}, code="invalid_input", status=422)
    with pytest.raises(HCloudError, match="invalid_input"):
        create(fake, server_type="cpx32", location="hel1", fallback_locations=["fsn1"])
    assert fake.attempts == [("cpx32", "hel1")]


def test_capacity_wait_retries_every_placement_until_capacity_returns():
    fake = FakeHetzner(rejected={("cpx32", "hel1"), ("cpx32", "fsn1")}, recover_after=4)
    server = create(
        fake,
        server_type="cpx32",
        location="hel1",
        fallback_locations=["fsn1"],
        capacity_wait_seconds=5,
        capacity_retry_seconds=0.01,
    )
    assert fake.attempts == [("cpx32", "hel1"), ("cpx32", "fsn1")] * 2 + [("cpx32", "hel1")]
    assert server["location"]["name"] == "hel1"
    assert int(server["labels"]["expires"]) >= int(time.time()) + 600 + 5 + 900 - 5
