"""Coral's model API spend (Anthropic Haiku curator, TypeSafe jev).

Neither provider exposes per-key billing to the tokens we hold, so the line
comes from coral's own meter: `/stats` reports tokens and list-price usd
since process start, and `usage_since_ms` says when that window began. The
monthly figure is that rate scaled to 30 days, flagged estimated. A window
shorter than MIN_WINDOW_S (a deploy just happened) is too noisy to scale and
is reported as unmeasured rather than as $0.

Override the endpoint with CORAL_STATS_URL.
"""

from __future__ import annotations

import datetime as dt
import os

import httpx

from mps.costs.projects import project_for
from mps.costs.types import LineItem, Period

STATS_URL = "https://api.coral.waow.tech/stats"
MIN_WINDOW_S = 3600
_MONTH_S = 30 * 86400


def line_items(stats: dict, now: dt.datetime) -> tuple[list[LineItem], list[str]]:
    since_ms = stats.get("usage_since_ms")
    if not isinstance(since_ms, int) or since_ms <= 0:
        raise RuntimeError("coral /stats has no usage_since_ms")
    window_s = now.timestamp() - since_ms / 1000
    if window_s < MIN_WINDOW_S:
        return [], [f"coral usage window is {window_s / 60:.0f} min, under {MIN_WINDOW_S // 60}"]

    scale = _MONTH_S / window_s
    hours = window_s / 3600
    project = project_for("coral")
    items: list[LineItem] = []
    for block, provider, service in (
        ("curator_llm", "anthropic", "coral:curator"),
        ("jev", "typesafe", "coral:jev"),
    ):
        meter = stats.get(block)
        if not isinstance(meter, dict) or "usd_since_start" not in meter:
            raise RuntimeError(f"coral /stats has no {block}.usd_since_start")
        cents = round(float(meter["usd_since_start"]) * scale * 100)
        if cents == 0:
            continue
        items.append(
            LineItem(
                provider=provider,
                project=project,
                service=service,
                amount=cents,
                estimated=True,
                usage=f"{meter.get('model', block)}, {meter.get('calls_today', 0)} calls today",
                note=(
                    f"coral's metered list-price spend over the last {hours:.1f}h "
                    "(since process start), scaled to 30 days"
                ),
            )
        )
    return items, []


class CoralApiConnector:
    name = "coral-api"

    def __init__(self) -> None:
        self.unmeasured: list[str] = []

    async def collect(self, period: Period) -> list[LineItem]:
        url = os.environ.get("CORAL_STATS_URL", STATS_URL)
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            items, self.unmeasured = line_items(resp.json(), dt.datetime.now(dt.UTC))
        return items
