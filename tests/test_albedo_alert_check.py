import pytest

from flows import albedo_alert_check as check

INCIDENT = {
    "last_fired": "2026-11-11T21:03:21.200833Z",
    "updates": [
        {"at": "2026-11-11T20:00:00Z", "agent": "albedo", "action": "alert.noted", "note": "x"},
        {
            "at": "2026-11-12T08:00:00Z",
            "agent": "claude-code",
            "action": "alert.noted",
            "note": "y",
        },
    ],
}


def test_a_note_before_the_last_firing_needs_a_fresh_check():
    assert not check.checked_since_last_firing(INCIDENT)


def test_a_note_after_the_last_firing_is_enough():
    later = {"at": "2026-11-12T09:00:00+00:00", "agent": "albedo", "action": "alert.noted"}
    assert check.checked_since_last_firing({**INCIDENT, "updates": [*INCIDENT["updates"], later]})


def test_finding_is_the_last_json_object_in_the_output():
    output = (
        'I fetched {"status": 503} from the endpoint.\n'
        '{"matters": true, "message": "typeahead freshness has returned 503 since 21:03Z."}'
    )
    finding = check.parse_finding(output)
    assert finding.matters is True
    assert finding.message.startswith("typeahead freshness")


def test_output_without_a_finding_is_an_error():
    with pytest.raises(ValueError):
        check.parse_finding('curl said {"status": 503}')


def test_a_finding_is_written_into_the_ops_space_as_albedo(monkeypatch):
    import asyncio
    import json

    import httpx

    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith("createSession"):
            return httpx.Response(200, json={"accessJwt": "jwt", "did": check.ALBEDO_DID})
        return httpx.Response(200, json={"uri": "at://space-record", "cid": "c"})

    real = httpx.AsyncClient
    monkeypatch.setattr(
        check.httpx,
        "AsyncClient",
        lambda **kw: real(**kw, transport=httpx.MockTransport(handler)),
    )
    monkeypatch.setattr(check, "secret_sync", lambda name: "pw")

    uri = asyncio.run(
        check.write_finding("fleet-unhealthy/x", "fleet unhealthy: x", "it is down", "https://run")
    )

    assert uri == "at://space-record"
    write = seen[1]
    assert write.url.path == "/xrpc/com.atproto.space.createRecord"
    assert write.headers["authorization"] == "Bearer jwt"
    body = json.loads(write.content)
    assert body["space"] == check.OPS_SPACE
    assert body["repo"] == check.ALBEDO_DID
    assert body["record"]["$type"] == "tech.waow.ops.finding"
    assert body["record"]["alertKey"] == "fleet-unhealthy/x"
    assert body["record"]["message"] == "it is down"
