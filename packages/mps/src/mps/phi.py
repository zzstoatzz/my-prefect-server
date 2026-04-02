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


class CardPlan(BaseModel):
    """A card to create on semble."""

    card_type: str = Field(description="NOTE or URL")
    content: str = Field(description="text content (NOTE) or URL string (URL)")
    title: str | None = Field(default=None, description="optional title for the card")
    description: str | None = Field(
        default=None, description="optional description / context"
    )
    ref_key: str = Field(
        description="local reference key (e.g. 'card-1') for cross-referencing within the plan"
    )


class ConnectionPlan(BaseModel):
    """A connection to create between two cards."""

    source: str = Field(
        description="at:// URI of existing card, or ref_key of a new card in this plan"
    )
    target: str = Field(
        description="at:// URI of existing card, or ref_key of a new card in this plan"
    )
    connection_type: str = Field(description="e.g. related, builds-on, contrasts")
    note: str | None = Field(default=None, description="brief explanation of the link")


class CollectionPlan(BaseModel):
    """A collection to create or update."""

    name: str = Field(description="collection name (concise, 2-5 words)")
    description: str | None = Field(default=None)
    card_refs: list[str] = Field(
        description="at:// URIs or ref_keys of cards to include"
    )
    existing_collection_uri: str | None = Field(
        default=None,
        description="if set, add cards to this existing collection instead of creating new",
    )


class CurationPlan(BaseModel):
    """Structured output from the curation LLM — what (if anything) to promote to semble."""

    should_curate: bool = Field(
        description="false most days — only true when there is genuinely new knowledge worth promoting"
    )
    reasoning: str = Field(
        description="brief explanation of why curation is or isn't warranted"
    )
    cards: list[CardPlan] = Field(default_factory=list)
    connections: list[ConnectionPlan] = Field(default_factory=list)
    collections: list[CollectionPlan] = Field(default_factory=list)


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
