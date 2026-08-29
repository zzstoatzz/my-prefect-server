"""bufo traffic rollup: route normalization matches the zig backend, rust
access lines and zig spans land in the same buckets, and a day record is the
sum of its hours."""

from __future__ import annotations

import datetime as dt

from mps.bufo_traffic import (
    COLLECTION,
    DayTraffic,
    HourRow,
    normalize_route,
    parse_rows,
    rollup,
    status_class,
)


def test_normalize_route_matches_zig_vocabulary():
    assert normalize_route("/api/search") == "/api/search"
    assert normalize_route("/e/bufo-lgtm.png") == "/e/{name}"
    assert normalize_route("/e/{name}") == "/e/{name}"  # zig spans arrive normalized
    assert normalize_route("/static/index.html") == "/static/{file}"
    assert normalize_route("/wp-login.php") == "unmatched"
    assert normalize_route("") == "unmatched"


def test_status_class():
    assert status_class(200) == "2xx"
    assert status_class(404) == "4xx"
    assert status_class(502) == "5xx"


def _hour(day: int, hour: int) -> dt.datetime:
    return dt.datetime(2026, 8, day, hour, tzinfo=dt.UTC)


def test_rollup_sums_hours_routes_and_statuses():
    rows = [
        HourRow(_hour(28, 16), "/api/search", 200, 5, client="plyr.fm"),
        HourRow(_hour(28, 16), "/e/bufo-a.png", 200, 3),  # rust-style raw path
        HourRow(_hour(28, 16), "/e/{name}", 404, 1),  # zig-style normalized
        HourRow(_hour(28, 22), "/wp-login.php", 404, 2),
        HourRow(_hour(29, 0), "/api/search", 400, 1),
        HourRow(_hour(29, 0), None, None, 1),  # unparseable line still counts
    ]
    days = rollup(rows)
    assert sorted(days) == [dt.date(2026, 8, 28), dt.date(2026, 8, 29)]

    d28 = days[dt.date(2026, 8, 28)]
    assert d28.total == 11
    assert d28.hours[16] == 9 and d28.hours[22] == 2
    assert d28.by_route == {"/api/search": 5, "/e/{name}": 4, "unmatched": 2}
    assert d28.by_status == {"2xx": 8, "4xx": 3}
    assert d28.by_client == {"plyr.fm": 5, "unknown": 6}

    d29 = days[dt.date(2026, 8, 29)]
    assert d29.total == 2
    assert d29.by_route == {"/api/search": 1, "unmatched": 1}
    assert d29.by_status == {"4xx": 1, "unknown": 1}


def test_record_shape_and_rkey():
    day = DayTraffic(day=dt.date(2026, 8, 28))
    day.hours[3] = 7
    day.by_route["/api/search"] = 7
    day.by_status["2xx"] = 7
    day.by_client["status.zzstoatzz.io"] = 2
    day.by_client["pi-extensions"] = 5
    rec = day.to_record(dt.datetime(2026, 8, 29, 1, 0, tzinfo=dt.UTC))
    assert day.rkey == "2026-08-28"
    assert rec["$type"] == COLLECTION
    assert rec["day"] == "2026-08-28"
    assert rec["total"] == 7
    assert len(rec["hours"]) == 24 and rec["hours"][3] == 7
    assert rec["byRoute"] == {"/api/search": 7}
    assert rec["byStatus"] == {"2xx": 7}
    assert list(rec["byClient"].items()) == [("pi-extensions", 5), ("status.zzstoatzz.io", 2)]
    assert rec["generatedAt"] == "2026-08-29T01:00:00Z"


def test_parse_rows_reads_row_oriented_payload():
    payload = {
        "schema": {"fields": []},
        "data": [
            {"hour": "2026-08-28T16:00:00Z", "path": "/api/search", "status": 200, "requests": 4, "client": "plyr.fm"},
            {"hour": "2026-08-28T17:00:00Z", "path": None, "status": None, "requests": 1},
        ],
    }
    rows = parse_rows(payload)
    assert len(rows) == 2
    assert rows[0].hour == dt.datetime(2026, 8, 28, 16, tzinfo=dt.UTC)
    assert rows[0].path == "/api/search" and rows[0].status == 200 and rows[0].requests == 4
    assert rows[0].client == "plyr.fm"
    assert rows[1].path is None and rows[1].status is None and rows[1].client == "unknown"
