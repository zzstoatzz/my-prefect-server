"""Data models for phi bot memory (TurboPuffer observations + interactions)."""

from dataclasses import dataclass, field

from pydantic import BaseModel, Field


def clean_handle(handle: str) -> str:
    """Sanitize handle to match bot's namespace naming convention."""
    return handle.replace(".", "_").replace("@", "").replace("-", "_")


def restore_handle(ns_id: str) -> str:
    """Reverse-map a namespace ID back to a handle (best-effort)."""
    return ns_id.removeprefix("phi-users-").replace("_", ".")


class TagMerge(BaseModel):
    """A set of tags to merge into one canonical form."""

    canonical: str = Field(description="the preferred tag name (lowercase, hyphenated)")
    aliases: list[str] = Field(description="tags to rewrite to canonical")
    related: list[str] = Field(
        default_factory=list,
        description="distinct but related tags — link, don't merge",
    )


class TagCluster(BaseModel):
    """A thematic grouping of related tags."""

    name: str = Field(description="short name for the cluster theme")
    description: str = Field(description="what ties these tags together")
    tags: list[str] = Field(description="member tags")
    cohesion: float = Field(
        ge=0.0, le=1.0, description="how tightly related are these tags"
    )


class TagRelationship(BaseModel):
    """A discovered relationship between two distinct tags."""

    tag_a: str
    tag_b: str
    relationship_type: str = Field(description="e.g. related, subtopic, overlapping")
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: str = Field(description="brief explanation of why these are related")


@dataclass
class PhiObservation:
    handle: str
    observation_id: str
    content: str
    tags: list[str] = field(default_factory=list)
    created_at: str = ""


@dataclass
class PhiInteraction:
    handle: str
    interaction_id: str
    content: str  # "user: ...\nbot: ..."
    created_at: str = ""
