from enum import StrEnum

from pydantic import BaseModel


class SectionAccent(StrEnum):
    red = "red"  # urgent, blocked, overdue
    amber = "amber"  # warnings, going stale
    emerald = "emerald"  # positive, quick wins, ready
    sky = "sky"  # informational, watching
    violet = "violet"  # features, enhancements


class SectionIcon(StrEnum):
    alert = "alert"  # exclamation triangle
    clock = "clock"  # hourglass / stale
    check = "check"  # checkmark / ready
    eye = "eye"  # watching
    bolt = "bolt"  # quick wins
    bookmark = "bookmark"  # docs, reference


class SectionPriority(StrEnum):
    high = "high"  # full-width, larger text
    normal = "normal"  # default 2-col grid
    low = "low"  # compact, de-emphasized


class BriefingItem(BaseModel):
    """A reference to a hub item with agent commentary."""

    item_id: str  # matches Card.id: "github:prefecthq/prefect#1234"
    note: str  # 1-line context: "stale 2 weeks, might be blocked"
    highlight: bool = False


class BriefingSection(BaseModel):
    """A themed group of items."""

    title: str  # e.g. "needs review", "quick wins", "going stale"
    summary: str  # 1-2 sentence section summary
    items: list[BriefingItem]
    accent: SectionAccent = SectionAccent.sky
    icon: SectionIcon = SectionIcon.eye
    priority: SectionPriority = SectionPriority.normal


class Briefing(BaseModel):
    """The agent's full dashboard briefing."""

    headline: str  # e.g. "3 items need attention today"
    sections: list[BriefingSection]
    generated_at: str  # ISO 8601
