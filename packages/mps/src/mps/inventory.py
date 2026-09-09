"""The project inventory shared by fleet checks, cost ownership, and Hub."""

from importlib.resources import files
from typing import Literal

from pydantic import BaseModel, Field, TypeAdapter


class EndpointRequest(BaseModel):
    method: Literal["GET", "POST"] = "GET"
    headers: dict[str, str] = Field(default_factory=dict)
    body: str | None = None


class EndpointCheck(BaseModel):
    name: str
    url: str = Field(pattern=r"^https://")
    href: str
    check: EndpointRequest = Field(default_factory=EndpointRequest)


class ProjectEntry(BaseModel):
    name: str
    costKeys: list[str]
    services: list[EndpointCheck]


def load_projects() -> list[ProjectEntry]:
    projects = TypeAdapter(list[ProjectEntry]).validate_json(
        files("mps").joinpath("projects.json").read_bytes()
    )
    urls = [s.url for p in projects for s in p.services]
    if not projects or len(set(urls)) != len(urls):
        raise ValueError("Project inventory is empty or has duplicate endpoint URLs")
    return projects
