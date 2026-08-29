"""bufo posts index: record parsing (images and video embeds, no-alt skips),
name/rkey mapping, grouping, and the compact record shapes."""

from __future__ import annotations

import datetime as dt

from mps.bufo_posts import (
    POSTS_COLLECTION,
    QUOTED_COLLECTION,
    BotPost,
    bufo_name_from_alt,
    bufo_record,
    bufo_rkey,
    group_by_bufo,
    parse_record,
    quoted_index_record,
    record_fingerprint,
)

BOT = "at://did:plc:rkpcbwi4rapfkm2huwrkspun/app.bsky.feed.post/"


def _value(alt: str | None, kind: str = "images", quoted: str | None = "at://did:plc:user1/app.bsky.feed.post/abc"):
    media = (
        {"$type": "app.bsky.embed.images", "images": [{"alt": alt, "image": {}}]}
        if kind == "images"
        else {"$type": "app.bsky.embed.video", "alt": alt, "video": {}}
    )
    return {
        "$type": "app.bsky.feed.post",
        "createdAt": "2026-05-02T19:47:27.000Z",
        "text": "",
        "embed": {
            "$type": "app.bsky.embed.recordWithMedia",
            "media": media,
            "record": {"$type": "app.bsky.embed.record", "record": {"uri": quoted, "cid": "x"}},
        },
    }


def test_name_and_rkey_mapping():
    assert bufo_name_from_alt("bufo on the ceiling") == "bufo-on-the-ceiling"
    assert bufo_rkey("bufo-on-the-ceiling") == "bufo-on-the-ceiling"
    assert bufo_rkey("bufo's-a-gamer-girl") == "bufo-s-a-gamer-girl"
    assert bufo_rkey("bufo+1") == "bufo-1"


def test_parse_images_and_video_embeds():
    p = parse_record(BOT + "3aaa", _value("bufo on the ceiling"))
    assert p == BotPost(
        rkey="3aaa",
        created_at=dt.datetime(2026, 5, 2, 19, 47, 27, tzinfo=dt.UTC),
        bufo="bufo-on-the-ceiling",
        quoted_uri="at://did:plc:user1/app.bsky.feed.post/abc",
    )
    assert p.quoted_did == "did:plc:user1"
    v = parse_record(BOT + "3bbb", _value("bufo furiously writes an epic update", kind="video"))
    assert v is not None and v.bufo == "bufo-furiously-writes-an-epic-update"


def test_posts_without_alt_or_date_are_skipped():
    assert parse_record(BOT + "3ccc", _value(None)) is None
    assert parse_record(BOT + "3ddd", _value("")) is None
    bad = _value("bufo x"); bad["createdAt"] = "not a date"
    assert parse_record(BOT + "3eee", bad) is None


def test_group_and_records():
    posts = [
        parse_record(BOT + "3a", _value("bufo a")),
        parse_record(BOT + "3b", {**_value("bufo a", quoted="at://did:plc:user2/app.bsky.feed.post/q"), "createdAt": "2026-06-01T00:00:00Z"}),
        parse_record(BOT + "3c", _value("bufo b", quoted=None)),
    ]
    groups = group_by_bufo(posts)
    assert sorted(groups) == ["bufo-a", "bufo-b"]
    assert [p.rkey for p in groups["bufo-a"]] == ["3b", "3a"]  # newest first

    now = dt.datetime(2026, 8, 29, tzinfo=dt.UTC)
    rec = bufo_record("bufo-a", groups["bufo-a"], now)
    assert rec["$type"] == POSTS_COLLECTION and rec["count"] == 2
    assert rec["posts"][0] == ["3b", 1780272000, "at://did:plc:user2/app.bsky.feed.post/q"]
    assert rec["generatedAt"] == "2026-08-29T00:00:00Z"

    idx = quoted_index_record(posts, now)
    assert idx["$type"] == QUOTED_COLLECTION
    assert idx["byDid"] == {"did:plc:user1": ["3a"], "did:plc:user2": ["3b"]}


def test_fingerprint_ignores_generated_at():
    a = {"count": 1, "posts": [["3a", 1, None]], "generatedAt": "x"}
    b = {"count": 1, "posts": [["3a", 1, None]], "generatedAt": "y"}
    c = {"count": 2, "posts": [["3a", 1, None], ["3b", 2, None]], "generatedAt": "y"}
    assert record_fingerprint(a) == record_fingerprint(b)
    assert record_fingerprint(a) != record_fingerprint(c)
