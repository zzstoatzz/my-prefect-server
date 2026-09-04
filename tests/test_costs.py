"""Tests for the cost connector hub: project mapping, snapshot aggregation,
and the invariant that an unmeasurable provider raises rather than reporting $0.
"""

from __future__ import annotations

import asyncio
import datetime as dt

import pytest
from mps.costs.projects import UNATTRIBUTED, project_for
from mps.costs.types import LineItem, Period, Snapshot


def test_project_mapping_longest_match_wins():
    # specific name beats the generic "plyr" substring
    assert project_for("plyr-transcoder") == "plyr.fm"
    assert project_for("plyr") == "plyr.fm"
    assert project_for("audio-prod") == "plyr.fm"
    assert project_for("audio-private-staging") == "plyr.fm"
    assert project_for("images-dev") == "plyr.fm"
    assert project_for("plyr-stats") == "plyr.fm"
    assert project_for("plyr.fm") == "plyr.fm"
    assert project_for("relay.waow") == "relays"  # not plyr's relay-api
    assert project_for("relay-api-staging") == "plyr.fm"
    assert project_for("leaflet-search-tap") == "standard.site"
    assert project_for("coral") == "trending"
    assert project_for("bufo-bot") == "bufo"
    assert project_for("something-random") == UNATTRIBUTED


def _snapshot(items: list[LineItem]) -> Snapshot:
    return Snapshot(
        generated_at=dt.datetime(2026, 6, 15, tzinfo=dt.UTC),
        period=Period(
            start=dt.datetime(2026, 5, 16, tzinfo=dt.UTC),
            end=dt.datetime(2026, 6, 15, tzinfo=dt.UTC),
        ),
        line_items=items,
    )


def test_snapshot_rollups_and_estimated_flag():
    snap = _snapshot(
        [
            LineItem(provider="fly", project="plyr.fm", service="a", amount=200, estimated=True),
            LineItem(provider="fly", project="relays", service="b", amount=300, estimated=False),
            LineItem(
                provider="hetzner", project="relays", service="c", amount=500, estimated=False
            ),
        ]
    )
    rec = snap.to_record()

    assert rec["total"] == 1000
    assert rec["$type"] == "io.zzstoatzz.cost.snapshot"

    by_provider = {r["key"]: r for r in rec["byProvider"]}
    assert by_provider["fly"]["amount"] == 500
    assert by_provider["fly"]["estimated"] is True  # any contributing item estimated
    assert by_provider["hetzner"]["estimated"] is False

    by_project = {r["key"]: r["amount"] for r in rec["byProject"]}
    assert by_project["relays"] == 800
    assert by_project["plyr.fm"] == 200

    # rollups sorted by amount desc
    assert [r["key"] for r in rec["byProvider"]] == ["fly", "hetzner"]


def test_record_omits_none_fields():
    snap = _snapshot(
        [LineItem(provider="neon", project="misc", service="db", amount=100, estimated=True)]
    )
    item = snap.to_record()["lineItems"][0]
    assert "usage" not in item and "note" not in item


def test_collect_connector_propagates_failure_instead_of_reporting_zero():
    from flows.costs import collect_connector

    class BrokenConnector:
        name = "broken"

        async def collect(self, period):
            raise RuntimeError("provider unavailable")

    assert collect_connector.retries == 3
    assert collect_connector.retry_delay_seconds == [2.0, 10.0, 30.0]
    with pytest.raises(RuntimeError, match="provider unavailable"):
        asyncio.run(collect_connector.fn(BrokenConnector(), Period.trailing_month()))


def test_costs_refuses_to_publish_when_any_provider_fails(monkeypatch):
    import flows.costs as costs_flow

    class Connector:
        def __init__(self, name):
            self.name = name

    async def fake_build_connectors():
        return [Connector("healthy"), Connector("fly")]

    async def fake_collect(connector, period):
        if connector.name == "fly":
            raise RuntimeError("machines API 500")
        return [
            LineItem(
                provider="healthy",
                project="misc",
                service="service",
                amount=100,
                estimated=False,
            )
        ]

    wrote = False

    async def fake_write_snapshot(snapshot):
        nonlocal wrote
        wrote = True

    monkeypatch.setattr(costs_flow, "build_connectors", fake_build_connectors)
    monkeypatch.setattr(costs_flow, "collect_connector", fake_collect)
    monkeypatch.setattr(costs_flow, "write_snapshot", fake_write_snapshot)

    with pytest.raises(RuntimeError, match="failed providers: fly"):
        asyncio.run(costs_flow.costs.fn(costs_flow.CostsConfig(dry_run=False)))
    assert wrote is False


def test_cloudflare_raises_without_account(monkeypatch):
    """An unmeasurable provider must raise, never silently report $0."""
    import asyncio

    from mps.costs.connectors.cloudflare import CloudflareConnector

    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "dummy")
    monkeypatch.delenv("CLOUDFLARE_ACCOUNT_ID", raising=False)

    async def fake_account(_client):
        return None

    monkeypatch.setattr("mps.costs.connectors.cloudflare._account_id", fake_account)

    with pytest.raises(RuntimeError, match="account id"):
        asyncio.run(CloudflareConnector().collect(Period.trailing_month()))


def test_cloudflare_degrades_when_r2_unmeasurable(monkeypatch, capsys):
    """If R2 analytics is unauthorized, omit R2 (no fake $0), warn, but still
    report real fixed costs — don't sink the whole connector."""
    import asyncio

    from mps.costs.connectors.cloudflare import CloudflareConnector

    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "dummy")
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "acct123")
    monkeypatch.setenv("CLOUDFLARE_FIXED_USD", "1.00")

    async def boom(*_args, **_kwargs):
        raise RuntimeError("not authorized for that account")

    monkeypatch.setattr("mps.costs.connectors.cloudflare._r2_stored_bytes_by_bucket", boom)

    items = asyncio.run(CloudflareConnector().collect(Period.trailing_month()))

    assert [i.service for i in items] == ["domains-and-plans"]
    assert items[0].amount == 100 and items[0].estimated is False
    assert "UNMEASURED" in capsys.readouterr().out


def test_cloudflare_r2_allocates_paid_storage_by_bucket(monkeypatch):
    """R2's 10 GB account free tier is applied once, then paid storage is
    attributed back to bucket-shaped line items."""
    import asyncio

    from mps.costs.connectors.cloudflare import CloudflareConnector

    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "dummy")
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "acct123")
    monkeypatch.delenv("CLOUDFLARE_FIXED_USD", raising=False)
    monkeypatch.delenv("CLOUDFLARE_FIXED_COSTS_JSON", raising=False)

    async def fake_buckets(*_args, **_kwargs):
        return {
            "audio-prod": 15_000_000_000,
            "misc-bucket": 5_000_000_000,
        }

    monkeypatch.setattr("mps.costs.connectors.cloudflare._r2_stored_bytes_by_bucket", fake_buckets)

    items = asyncio.run(CloudflareConnector().collect(Period.trailing_month()))

    assert [(i.service, i.project, i.amount) for i in items] == [
        ("r2:audio-prod", "plyr.fm", 11),
        ("r2:misc-bucket", UNATTRIBUTED, 4),
    ]


def test_cloudflare_fixed_costs_json_splits_resources(monkeypatch):
    import asyncio

    from mps.costs.connectors.cloudflare import CloudflareConnector

    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "dummy")
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "acct123")
    monkeypatch.setenv(
        "CLOUDFLARE_FIXED_COSTS_JSON",
        '{"plyr.fm": 1.0, "workers-paid-plan": {"amount": 4.0, "project": "shared"}}',
    )

    async def no_buckets(*_args, **_kwargs):
        return {}

    monkeypatch.setattr("mps.costs.connectors.cloudflare._r2_stored_bytes_by_bucket", no_buckets)

    items = asyncio.run(CloudflareConnector().collect(Period.trailing_month()))

    assert [(i.service, i.project, i.amount) for i in items] == [
        ("plyr.fm", "plyr.fm", 100),
        ("workers-paid-plan", "shared", 400),
    ]


def test_hetzner_merges_project_tokens_and_dedupes(monkeypatch):
    """Multiple project-scoped tokens (comma-separated in one var) are queried
    and merged; servers visible to more than one token are counted once."""
    import asyncio

    from mps.costs.connectors import hetzner

    monkeypatch.setenv("HCLOUD_TOKEN", "tok-a, tok-b")

    fake = {
        "tok-a": [
            {"id": 1, "name": "relay-node", "server_type": {"name": "cx22", "prices": []}},
        ],
        "tok-b": [
            {
                "id": 1,
                "name": "relay-node",
                "server_type": {"name": "cx22", "prices": []},
            },  # dup id
            {"id": 2, "name": "pds-zzstoatzz-io", "server_type": {"name": "cx22", "prices": []}},
        ],
    }

    async def fake_servers(token):
        return fake[token]

    async def fake_volumes(token):
        return []

    monkeypatch.setattr(hetzner, "_servers", fake_servers)
    monkeypatch.setattr(hetzner, "_volumes", fake_volumes)

    items = asyncio.run(hetzner.HetznerConnector().collect(Period.trailing_month()))
    services = sorted(i.service for i in items)
    assert services == ["pds-zzstoatzz-io", "relay-node"]  # id=1 counted once


def test_hetzner_prices_volumes_at_per_gb_list(monkeypatch):
    """Volumes bill per provisioned GB (the dominant cost for archive boxes like
    stream); the connector must emit a line per volume, attributed by volume
    name with fallback to the attached server, and never double-count across
    tokens."""
    import asyncio

    from mps.costs.connectors import hetzner

    monkeypatch.setenv("HCLOUD_TOKEN", "tok-a")

    async def fake_servers(token):
        return [
            {"id": 7, "name": "stream-cx43", "server_type": {"name": "cx43", "prices": []}},
        ]

    async def fake_volumes(token):
        return [
            {"id": 70, "name": "stream-archive", "size": 3000, "server": 7},
            {"id": 71, "name": "volume-hel1-1234", "size": 100, "server": 7},  # auto-name
        ]

    async def fake_price(token):
        return 7.67  # cents per GB-month

    monkeypatch.setattr(hetzner, "_servers", fake_servers)
    monkeypatch.setattr(hetzner, "_volumes", fake_volumes)
    monkeypatch.setattr(hetzner, "_volume_gb_month_cents", fake_price)

    items = asyncio.run(hetzner.HetznerConnector().collect(Period.trailing_month()))
    by_service = {i.service: i for i in items}
    archive = by_service["stream-archive:volume"]
    assert archive.amount == round(3000 * 7.67)  # 23010 cents ≈ €230.10
    assert archive.project == "stream"
    # auto-named volume falls back to the attached server's project
    assert by_service["volume-hel1-1234:volume"].project == "stream"
