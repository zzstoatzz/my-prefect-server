"""typeahead-handle-repair fixes handles the pre-2026-09-25 #identity path
wrote stale. Its contract: only a handle claimed by the DID document is a
candidate, genesis ops are not repair candidates, and the write is a
compare-and-set that cannot clobber a newer live ingester write."""

from flows.typeahead_handle_repair import (
    REPAIR_SQL,
    handle_from_aka,
    needs_check,
    pds_from_doc,
    repair_statement,
    tail_claims,
    typeahead_handle_repair,
)


def test_handle_is_first_at_uri_lowercased():
    assert (
        handle_from_aka(["https://x.example", "at://MolTrust.ch", "at://other.example"])
        == "moltrust.ch"
    )
    assert handle_from_aka(["https://x.example"]) is None
    assert handle_from_aka(None) is None


def test_pds_from_doc():
    doc = {"service": [{"id": "#atproto_pds", "serviceEndpoint": "https://pds.example"}]}
    assert pds_from_doc(doc) == "https://pds.example"
    assert pds_from_doc({}) is None


def test_tail_claims_skip_genesis_tombstone_and_nullified_and_keep_latest():
    ops = [
        {
            "did": "did:plc:new",
            "operation": {"prev": None, "alsoKnownAs": ["at://new.bsky.social"]},
        },
        {"did": "did:plc:a", "operation": {"prev": "x", "alsoKnownAs": ["at://first.example"]}},
        {"did": "did:plc:a", "operation": {"prev": "y", "alsoKnownAs": ["at://second.example"]}},
        {"did": "did:plc:dead", "operation": {"prev": "x", "type": "plc_tombstone"}},
        {
            "did": "did:plc:n",
            "nullified": True,
            "operation": {"prev": "x", "alsoKnownAs": ["at://n.example"]},
        },
    ]
    assert tail_claims(ops) == {"did:plc:a": "second.example"}


def test_only_existing_rows_with_a_different_handle_are_candidates():
    assert needs_check("moltrust.bsky.social", "moltrust.ch")
    assert not needs_check("MolTrust.ch", "moltrust.ch")
    assert not needs_check(None, "moltrust.ch")


def test_write_is_compare_and_set_on_the_handle_we_read():
    stmt = repair_statement("did:plc:a", "old.bsky.social", "new.example", None)
    assert stmt["sql"] == REPAIR_SQL
    assert "WHERE did = ?1 AND handle = ?4" in REPAIR_SQL
    assert [a["value"] for a in stmt["args"]] == ["did:plc:a", "new.example", "", "old.bsky.social"]
    # the search overlay syncs on updated_at; profile_checked_at must not be
    # reset, or getProfiles hides the counts until enrich-backfill returns
    assert "updated_at = unixepoch()" in REPAIR_SQL
    assert "profile_checked_at" not in REPAIR_SQL


def test_flow_is_time_bounded():
    assert typeahead_handle_repair.timeout_seconds == 6 * 3600
