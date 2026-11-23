import httpx
from mps import atproto


def test_create_session_logs_in_at_the_did_document_pds(monkeypatch):
    seen = {}

    def fake_post(url, json, timeout):
        seen["url"] = url
        seen["json"] = json
        return httpx.Response(
            200, json={"did": json["identifier"]}, request=httpx.Request("POST", url)
        )

    monkeypatch.setattr(atproto, "resolve_pds", lambda did: "https://pds.example")
    monkeypatch.setattr(atproto.httpx, "post", fake_post)

    session = atproto.create_session("did:plc:abc", "pw")

    assert seen["url"] == "https://pds.example/xrpc/com.atproto.server.createSession"
    assert seen["json"]["identifier"] == "did:plc:abc"
    assert session["did"] == "did:plc:abc"
