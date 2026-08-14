from mps.mcp_atlas import (
    MAX_DESCRIPTION,
    MAX_TOOLS,
    handle_from_did_doc,
    normalize_record,
    pds_from_did_doc,
)

DID = "did:plc:abc123"
URI = f"at://{DID}/tech.waow.mcp.server/3abc"


def test_normalize_minimal_record():
    entry = normalize_record(
        DID, "alice.test", URI, {"name": "partscout", "description": "prices gear"}
    )
    assert entry == {
        "did": DID,
        "handle": "alice.test",
        "uri": URI,
        "name": "partscout",
        "description": "prices gear",
        "repo": None,
        "url": None,
        "manifest": None,
        "framework": None,
        "tools": [],
        "createdAt": None,
    }


def test_normalize_full_record():
    entry = normalize_record(
        DID,
        "alice.test",
        URI,
        {
            "name": "partscout",
            "description": "prices gear",
            "repo": "https://example.com/repo",
            "url": "https://example.com/mcp",
            "manifest": "https://example.com/fastmcp.json",
            "framework": "fastmcp",
            "tools": ["search_gear", "price_snapshot"],
            "createdAt": "2026-08-13T00:00:00Z",
        },
    )
    assert entry is not None
    assert entry["repo"] == "https://example.com/repo"
    assert entry["url"] == "https://example.com/mcp"
    assert entry["framework"] == "fastmcp"
    assert entry["tools"] == ["search_gear", "price_snapshot"]
    assert entry["createdAt"] == "2026-08-13T00:00:00Z"


def test_missing_required_fields_dropped():
    assert normalize_record(DID, None, URI, {}) is None
    assert normalize_record(DID, None, URI, {"name": "x"}) is None
    assert normalize_record(DID, None, URI, {"name": "  ", "description": "y"}) is None
    assert normalize_record(DID, None, URI, {"name": 3, "description": "y"}) is None


def test_hostile_record_clamped():
    entry = normalize_record(
        DID,
        None,
        URI,
        {
            "name": "n" * 1000,
            "description": "d" * 10_000,
            "repo": "javascript:alert(1)",
            "url": "ftp://nope",
            "tools": ["ok"] + [42, None, ""] + ["t"] * 200,
            "framework": 7,
            "createdAt": {"$bad": True},
        },
    )
    assert entry is not None
    assert len(entry["name"]) == 128
    assert len(entry["description"]) == MAX_DESCRIPTION
    assert entry["repo"] is None
    assert entry["url"] is None
    assert entry["framework"] is None
    assert len(entry["tools"]) == MAX_TOOLS
    assert entry["createdAt"] is None


def test_did_doc_helpers():
    doc = {
        "alsoKnownAs": ["at://alice.test"],
        "service": [
            {"id": "#other", "serviceEndpoint": "https://nope.example"},
            {"id": "#atproto_pds", "serviceEndpoint": "https://pds.example.com"},
        ],
    }
    assert handle_from_did_doc(doc) == "alice.test"
    assert pds_from_did_doc(doc) == "https://pds.example.com"
    assert handle_from_did_doc({}) is None
    assert (
        pds_from_did_doc({"service": [{"id": "#atproto_pds", "serviceEndpoint": 5}]})
        is None
    )
