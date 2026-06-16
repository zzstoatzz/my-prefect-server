"""Shared types for the cost connector hub.

Amounts are integer minor units (USD cents) end to end — no floats touch money
until rendering. Each connector returns LineItems; the flow aggregates them into
a Snapshot that mirrors the io.zzstoatzz.cost.snapshot lexicon.
"""

from __future__ import annotations

import datetime as dt
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, Field


class Period(BaseModel):
    """The billing window a collection run covers."""

    start: dt.datetime
    end: dt.datetime

    @classmethod
    def trailing_month(cls, now: dt.datetime | None = None) -> "Period":
        end = now or dt.datetime.now(dt.UTC)
        start = end - dt.timedelta(days=30)
        return cls(start=start, end=end)


class LineItem(BaseModel):
    """One project's spend on one service from one provider, in cents."""

    provider: str
    project: str
    service: str
    amount: int = Field(description="cost for the period in cents")
    estimated: bool = Field(
        description="true if derived from a heuristic rather than a billed figure"
    )
    usage: str | None = None
    note: str | None = None


class Rollup(BaseModel):
    key: str
    amount: int
    estimated: bool = False


class Snapshot(BaseModel):
    """Mirrors io.zzstoatzz.cost.snapshot. `to_record()` produces the PDS body."""

    generated_at: dt.datetime
    period: Period
    currency: str = "USD"
    line_items: list[LineItem]

    def _rollup(self, key: str) -> list[Rollup]:
        groups: dict[str, list[LineItem]] = {}
        for item in self.line_items:
            groups.setdefault(getattr(item, key), []).append(item)
        rollups = [
            Rollup(
                key=k,
                amount=sum(i.amount for i in items),
                estimated=any(i.estimated for i in items),
            )
            for k, items in groups.items()
        ]
        return sorted(rollups, key=lambda r: r.amount, reverse=True)

    @property
    def total(self) -> int:
        return sum(i.amount for i in self.line_items)

    def to_record(self) -> dict:
        return {
            "$type": "io.zzstoatzz.cost.snapshot",
            "generatedAt": _iso(self.generated_at),
            "periodStart": _iso(self.period.start),
            "periodEnd": _iso(self.period.end),
            "currency": self.currency,
            "total": self.total,
            "byProvider": [r.model_dump() for r in self._rollup("provider")],
            "byProject": [r.model_dump() for r in self._rollup("project")],
            "lineItems": [
                {k: v for k, v in i.model_dump().items() if v is not None}
                for i in self.line_items
            ],
        }


@runtime_checkable
class Connector(Protocol):
    """A billing source. `collect` is best-effort: it should return whatever it
    can and raise only on hard auth/transport failures, so one dead provider
    doesn't sink the whole snapshot (the flow wraps each in its own task)."""

    name: str

    async def collect(self, period: Period) -> list[LineItem]: ...


def _iso(d: dt.datetime) -> str:
    return d.astimezone(dt.UTC).isoformat().replace("+00:00", "Z")
