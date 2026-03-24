"""Data models for phi bot memory (TurboPuffer observations + interactions)."""

from dataclasses import dataclass, field


def clean_handle(handle: str) -> str:
    """Sanitize handle to match bot's namespace naming convention."""
    return handle.replace(".", "_").replace("@", "").replace("-", "_")


def restore_handle(ns_id: str) -> str:
    """Reverse-map a namespace ID back to a handle (best-effort)."""
    return ns_id.removeprefix("phi-users-").replace("_", ".")


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
