"""typeahead-identity-hourly replaces the identity phase of typeahead's
worker cron, which the 15-minute wall limit killed in 120 of 140 runs. The
contract it must keep: every selected DID is stamped whether or not it
resolved, handle.invalid never becomes a handle, and the writes are
idempotent so the retrying Turso client can replay them."""

from flows.typeahead_identity import resolve_batch, statements_for, typeahead_identity_hourly


def test_handle_invalid_and_missing_handles_do_not_resolve():
    out = resolve_batch(
        [
            {"did": "did:plc:a", "handle": "a.bsky.social"},
            {"did": "did:plc:b", "handle": "handle.invalid"},
            {"did": "did:plc:c"},
            {"handle": "orphan.example"},
        ]
    )
    assert out == [("did:plc:a", "a.bsky.social")]


def test_every_selected_did_is_stamped_resolved_or_not():
    stmts = statements_for(["did:plc:a", "did:plc:b"], [("did:plc:a", "a.bsky.social")])
    sets_handle = [s for s in stmts if "SET handle = ?2" in s["sql"]]
    stamps = [s for s in stmts if "AND handle = ''" in s["sql"]]
    assert [s["args"][1]["value"] for s in sets_handle] == ["a.bsky.social"]
    assert [s["args"][0]["value"] for s in stamps] == ["did:plc:a", "did:plc:b"]
    # the stamp only touches rows still without a handle, so replaying it after
    # the handle write cannot undo anything
    assert all("WHERE did = ?1 AND handle = ''" in s["sql"] for s in stamps)


def test_flow_budget_fits_its_timeout():
    fn = typeahead_identity_hourly
    assert fn.timeout_seconds > 2400 + 120  # budget + a final page + retries
