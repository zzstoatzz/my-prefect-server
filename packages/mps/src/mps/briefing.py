from enum import StrEnum

from pydantic import BaseModel, Field


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

    item_id: str = Field(examples=["github:prefecthq/prefect#1234", "tangled:zat#42"])
    note: str = Field(examples=["stale 2 weeks, might be blocked", "ready to merge, just needs a rebase"])
    highlight: bool = False


class BriefingSection(BaseModel):
    """A themed group of items."""

    title: str = Field(examples=["needs review", "quick wins", "going stale"])
    summary: str = Field(description="1-2 sentence section summary")
    items: list[BriefingItem]
    accent: SectionAccent = SectionAccent.sky
    icon: SectionIcon = SectionIcon.eye
    priority: SectionPriority = SectionPriority.normal


class Briefing(BaseModel):
    """The agent's full dashboard briefing."""

    title: str = Field(description="2-3 word vibe", examples=["monday triage", "quiet week", "bug cluster"])
    headline: str = Field(description="1-2 sentence summary of what's going on and what to pay attention to", examples=["3 PRs need review and a bug is blocking the release. tangled activity is quiet."])
    sections: list[BriefingSection]
    generated_at: str
