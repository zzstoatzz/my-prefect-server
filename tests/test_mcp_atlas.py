from mps.mcp_atlas import (
    MAX_DESCRIPTION,
    MAX_TOOLS,
    atlas_positions,
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
        "language": None,
        "transport": None,
        "tools": [],
        "environment": [],
        "packages": [],
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
            "language": "typescript",
            "transport": "http",
            "tools": [
                {"name": "search_gear"},
                {"name": "price_snapshot", "description": "distribution of asks"},
            ],
            "environment": [
                {"name": "EBAY_CLIENT_ID", "required": True, "description": "app id"},
                {"name": "EBAY_TIMEOUT"},
                "junk",
                {"required": True},
            ],
            "packages": [
                {"registry": "pypi", "identifier": "partscout"},
                {"registry": "npm"},
                7,
            ],
            "createdAt": "2026-08-13T00:00:00Z",
        },
    )
    assert entry is not None
    assert entry["environment"] == [
        {"name": "EBAY_CLIENT_ID", "required": True, "description": "app id"},
        {"name": "EBAY_TIMEOUT", "required": False, "description": None},
    ]
    assert entry["packages"] == [{"registry": "pypi", "identifier": "partscout"}]
    assert entry["repo"] == "https://example.com/repo"
    assert entry["url"] == "https://example.com/mcp"
    assert entry["framework"] == "fastmcp"
    assert entry["transport"] == "http"
    assert entry["language"] == "typescript"
    assert entry["tools"] == [
        {"name": "search_gear", "description": None},
        {"name": "price_snapshot", "description": "distribution of asks"},
    ]
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
            "tools": ["ok"]
            + [42, None, ""]
            + [{"name": "t" * 500, "description": 9}] * 200,
            "transport": "carrier-pigeon",
            "framework": 7,
            "createdAt": {"$bad": True},
        },
    )
    assert entry is not None
    assert len(entry["name"]) == 64
    assert len(entry["description"]) == MAX_DESCRIPTION
    assert entry["repo"] is None
    assert entry["url"] is None
    assert entry["framework"] is None
    assert len(entry["tools"]) == MAX_TOOLS
    assert entry["tools"][0] == {"name": "ok", "description": None}
    assert len(entry["tools"][1]["name"]) == 128
    assert entry["tools"][1]["description"] is None
    assert entry["transport"] is None
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


def _entry(name, description, tools):
    return {
        "name": name,
        "description": description,
        "framework": None,
        "tools": [{"name": t, "description": None} for t in tools],
    }


def test_positions_edge_counts():
    assert atlas_positions([]) == []
    assert atlas_positions([_entry("a", "b", [])]) == [(0.5, 0.5)]


def test_positions_semantic_neighbors():
    entries = [
        _entry(
            "pdsx", "crud over atproto pds records", ["list_records", "create_record"]
        ),
        _entry(
            "tangled",
            "git collaboration records on atproto pds",
            ["list_records", "get_record"],
        ),
        _entry(
            "partscout", "pricing computer hardware ebay listings", ["price_snapshot"]
        ),
    ]
    pts = atlas_positions(entries)
    assert len(pts) == 3
    assert all(0.0 <= x <= 1.0 and 0.0 <= y <= 1.0 for x, y in pts)
    assert pts == atlas_positions(entries)  # deterministic

    def dist(a, b):
        return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5

    # the two record-CRUD servers should sit closer to each other than
    # either sits to the ebay pricer
    assert dist(pts[0], pts[1]) < dist(pts[0], pts[2])
    assert dist(pts[0], pts[1]) < dist(pts[1], pts[2])
