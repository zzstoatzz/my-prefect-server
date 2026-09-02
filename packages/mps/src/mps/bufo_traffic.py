"""find-bufo.com request traffic, rolled up from logfire into one PDS record per
UTC day (`com.find-bufo.traffic`, rkey=YYYY-MM-DD).

logfire only answers 14-day windows and keeps a bounded history; the PDS record
is the durable, public copy the bot stats page draws "requests all time" from.
records are small (24 hourly counts plus a route and status breakdown), so a
year is ~365 tiny records and the page can page through all of them.

two sources cover the two backends that have served find-bufo.com:

- the zig backend (since 2026-08-28) emits one `HTTP <method> <route>` span per
  request with `url.path` (already a normalized route) and
  `http.response.status_code`
- the retired rust backend logged an actix access line per request:
  `<ip> "GET /e/bufo.png HTTP/1.1" 200 …` — the route and status are parsed
  out and normalized the same way, so the series is continuous across the
  cutover
"""

from __future__ import annotations

import datetime as dt
import re
from collections import defaultdict
from dataclasses import dataclass, field

COLLECTION = "com.find-bufo.traffic"
SERVICE = "find-bufo"

# the zig backend's route vocabulary (server/src/server.zig `routeFor`)
KNOWN_ROUTES = (
    "/",
    "/api/health",
    "/api/search",
    "/api/similar",
    "/api/bufo-of-the-month",
    "/api/image",
    "/bufo-of-the-month",
)
UNMATCHED = "unmatched"

_ACCESS_LINE = re.compile(r'"(?P<method>[A-Z]+) (?P<path>[^ ?"]+)[^"]*" (?P<status>\d{3}) ')


def normalize_route(path: str) -> str:
    """the same normalization the zig backend applies before it emits a span,
    so pre-cutover rust access logs land in identical buckets."""
    if path in KNOWN_ROUTES:
        return path
    if path.startswith("/e/"):
        return "/e/{name}"
    if path.startswith("/static/"):
        return "/static/{file}"
    return UNMATCHED


def status_class(code: int) -> str:
    return f"{code // 100}xx"


# one logfire query serves both backends: zig spans carry the fields as
# attributes, rust access logs carry them inside the message. the route is
# normalized IN SQL (the same vocabulary as `normalize_route`) and the status is
# reduced to its class, so the result is at most hours × routes × classes
# rows — grouping by raw path once returned exactly the 10k row cap, silently
# truncated, because every /e/<name> and scanner url was its own group.
QUERY = f"""
WITH hits AS (
  SELECT
    date_trunc('hour', start_timestamp) AS hour,
    CASE
      WHEN kind = 'span' THEN attributes->>'url.path'
      ELSE regexp_match(message, '"[A-Z]+ ([^ ?"]+)[^"]*" [0-9]{{3}} ')[1]
    END AS raw_path,
    CASE
      WHEN kind = 'span' THEN (attributes->>'http.response.status_code')::int
      ELSE regexp_match(message, '"[A-Z]+ [^"]*" ([0-9]{{3}}) ')[1]::int
    END AS status,
    CASE
      WHEN kind = 'span' THEN coalesce(attributes->>'client', 'unknown')
      ELSE 'unknown'
    END AS client
  FROM records
  WHERE (kind = 'span' AND service_name = '{SERVICE}' AND span_name LIKE 'HTTP %')
     OR (kind = 'log' AND service_name = 'unknown_service'
         AND (span_name LIKE '%"GET %' OR span_name LIKE '%"POST %'
              OR span_name LIKE '%"HEAD %' OR span_name LIKE '%"OPTIONS %'))
)
SELECT
  hour,
  CASE
    WHEN raw_path IN ({", ".join(repr(r) for r in KNOWN_ROUTES)}) THEN raw_path
    WHEN raw_path LIKE '/e/%' THEN '/e/{{name}}'
    WHEN raw_path LIKE '/static/%' THEN '/static/{{file}}'
    ELSE '{UNMATCHED}'
  END AS path,
  status / 100 * 100 AS status,
  client,
  count(*) AS requests
FROM hits
GROUP BY 1, 2, 3, 4
ORDER BY 1
"""

@dataclass
class HourRow:
    """one (hour, path, status) row from the query. `path` is raw for rust
    lines and already-normalized for zig spans; `normalize_route` is
    idempotent so both are treated the same."""

    hour: dt.datetime
    path: str | None
    status: int | None
    requests: int
    client: str = "unknown"


@dataclass
class DayTraffic:
    day: dt.date
    hours: list[int] = field(default_factory=lambda: [0] * 24)
    by_route: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    by_status: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    # which app sent the request: X-Client header, else Origin/Referer host,
    # else "unknown" (the zig server attributes it on the span; rust-era logs
    # carry nothing, so they count as unknown)
    by_client: dict[str, int] = field(default_factory=lambda: defaultdict(int))

    @property
    def total(self) -> int:
        return sum(self.hours)

    @property
    def rkey(self) -> str:
        return self.day.isoformat()

    def to_record(self, generated_at: dt.datetime) -> dict:
        return {
            "$type": COLLECTION,
            "day": self.rkey,
            "total": self.total,
            "hours": list(self.hours),
            "byRoute": dict(sorted(self.by_route.items(), key=lambda kv: -kv[1])),
            "byStatus": dict(sorted(self.by_status.items())),
            "byClient": dict(sorted(self.by_client.items(), key=lambda kv: -kv[1])),
            "generatedAt": generated_at.astimezone(dt.UTC).isoformat().replace("+00:00", "Z"),
        }


def rollup(rows: list[HourRow]) -> dict[dt.date, DayTraffic]:
    """fold query rows into per-day records. rows with no parseable path are
    still counted (as `unmatched`) — a request is a request."""
    days: dict[dt.date, DayTraffic] = {}
    for r in rows:
        hour = r.hour.astimezone(dt.UTC)
        day = days.setdefault(hour.date(), DayTraffic(day=hour.date()))
        day.hours[hour.hour] += r.requests
        day.by_route[normalize_route(r.path or "")] += r.requests
        day.by_status[status_class(r.status) if r.status is not None else "unknown"] += r.requests
        day.by_client[r.client or "unknown"] += r.requests
    return days


def parse_rows(payload: dict) -> list[HourRow]:
    """the logfire query api (POST /v2/query, Accept: application/json) returns
    {"schema": {...}, "data": [{"hour": ..., "path": ..., "status": ..., "requests": ...}, ...]}"""
    out: list[HourRow] = []
    for row in payload["data"]:
        status = row.get("status")
        out.append(
            HourRow(
                hour=dt.datetime.fromisoformat(row["hour"].replace("Z", "+00:00")),
                path=row.get("path"),
                status=int(status) if status is not None else None,
                requests=int(row["requests"]),
                client=row.get("client") or "unknown",
            )
        )
    return out
