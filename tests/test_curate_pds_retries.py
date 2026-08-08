"""Regression tests for curate surviving transient bsky.social 500s.

curate-622f8e09 died when listRecords returned a one-off 500 mid-pagination.
_list_records now retries 5xx and transport errors with backoff instead of
letting a single blip kill the run.
"""

from unittest.mock import patch

import httpx
import pytest

from flows.curate import PHI_DID, _get_json_with_retries, _list_records

PAGE_ONE = {"records": [{"uri": "at://x/1"}], "cursor": "c1"}
PAGE_TWO = {"records": [{"uri": "at://x/2"}]}


def test_list_records_survives_transient_500():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(200, json=PAGE_ONE)
        if calls["n"] == 2:
            return httpx.Response(500, text="InternalServerError")
        return httpx.Response(200, json=PAGE_TWO)

    with patch("flows.curate.time.sleep"):
        records = _list_records(
            PHI_DID, "network.cosmik.card", transport=httpx.MockTransport(handler)
        )

    assert [r["uri"] for r in records] == ["at://x/1", "at://x/2"]
    assert calls["n"] == 3


def test_get_json_with_retries_gives_up_on_persistent_500():
    transport = httpx.MockTransport(lambda r: httpx.Response(500))
    with (
        patch("flows.curate.time.sleep") as sleep,
        httpx.Client(base_url="https://example.test", transport=transport) as client,
        pytest.raises(httpx.HTTPStatusError),
    ):
        _get_json_with_retries(client, "/x", {}, attempts=3)
    assert sleep.call_count == 2


def test_get_json_with_retries_survives_transport_error():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectError("reset", request=request)
        return httpx.Response(200, json={"ok": True})

    with (
        patch("flows.curate.time.sleep"),
        httpx.Client(
            base_url="https://example.test", transport=httpx.MockTransport(handler)
        ) as client,
    ):
        assert _get_json_with_retries(client, "/x", {}) == {"ok": True}
