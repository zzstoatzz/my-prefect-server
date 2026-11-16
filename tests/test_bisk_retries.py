"""Bisk HTTP failures must propagate instead of producing partial data."""

import httpx
import pytest

from flows.bisk import _get


@pytest.mark.parametrize(
    "failure",
    [
        httpx.RemoteProtocolError("Server disconnected without sending a response."),
        httpx.ConnectError("connection refused"),
    ],
)
async def test_get_propagates_transport_errors(failure: Exception):
    """Transport failures must reach the task, not be swallowed into a result."""

    class Boom:
        async def get(self, url: str, params: dict) -> httpx.Response:
            raise failure

    with pytest.raises(type(failure)):
        await _get(Boom(), "https://example.invalid/xrpc/whatever", {})


async def test_get_raises_on_error_status():
    """A 502 from constellation must fail the task, not return an empty dict."""
    request = httpx.Request("GET", "https://example.invalid/xrpc/whatever")

    class Gateway:
        async def get(self, url: str, params: dict) -> httpx.Response:
            return httpx.Response(502, request=request, text="bad gateway")

    with pytest.raises(httpx.HTTPStatusError):
        await _get(Gateway(), "https://example.invalid/xrpc/whatever", {})
