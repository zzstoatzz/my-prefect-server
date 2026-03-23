from pydantic import BaseModel


class BriefingItem(BaseModel):
    """A reference to a hub item with agent commentary."""

    item_id: str  # matches Card.id: "github:prefecthq/prefect#1234"
    note: str  # 1-line context: "stale 2 weeks, might be blocked"


class BriefingSection(BaseModel):
    """A themed group of items."""

    title: str  # e.g. "needs review", "quick wins", "going stale"
    summary: str  # 1-2 sentence section summary
    items: list[BriefingItem]


class Briefing(BaseModel):
    """The agent's full dashboard briefing."""

    headline: str  # e.g. "3 items need attention today"
    sections: list[BriefingSection]
    generated_at: str  # ISO 8601
