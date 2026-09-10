import httpx
import pytest
from mps import tangled

PULL = "at://did:plc:gardener/sh.tangled.repo.pull/test"


def test_stale_revision_rejected_before_login(monkeypatch):
    monkeypatch.setattr(tangled, "get_record", lambda uri: {"cid": "new", "value": {}})
    monkeypatch.setattr(tangled, "login", lambda *args: pytest.fail("must not authenticate"))
    with pytest.raises(ValueError, match="changed"):
        tangled.append_round(PULL, "patch", "note", "handle", "password", expected_cid="old")


def test_concurrent_publication_uses_compare_and_swap(monkeypatch):
    monkeypatch.setattr(
        tangled, "get_record", lambda uri: {"cid": "reviewed", "value": {"rounds": []}}
    )
    monkeypatch.setattr(
        tangled, "login", lambda *args: ("https://pds.example", "did:plc:gardener", {})
    )
    writes = []

    def post(url, **kwargs):
        request = httpx.Request("POST", url)
        if url.endswith("uploadBlob"):
            return httpx.Response(200, json={"blob": {"ref": {"$link": "patch"}}}, request=request)
        writes.append(kwargs["json"])
        # Another writer changed the record after our read.
        return httpx.Response(400, json={"error": "InvalidSwap"}, request=request)

    monkeypatch.setattr(tangled.httpx, "post", post)
    with pytest.raises(httpx.HTTPStatusError):
        tangled.append_round(PULL, "patch", "note", "handle", "password", expected_cid="reviewed")
    assert len(writes) == 1
    assert writes[0]["swapRecord"] == "reviewed"
