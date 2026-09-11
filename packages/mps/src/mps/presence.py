"""The private home-presence record and its owner-authenticated read path."""

from datetime import datetime
from typing import Literal

import httpx
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

PDS = "https://spaces-alpha.host.bsky.network"
OWNER = "did:plc:x5vcg5tj466g64de3jvvkzjg"
SPACE_TYPE = "io.zzstoatzz.home.space"
COLLECTION = "io.zzstoatzz.home.presence"
SPACE = f"at://{OWNER}/space/{SPACE_TYPE}/home"


class PresenceUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    state: Literal["home", "away"]
    observedAt: AwareDatetime


class Presence(BaseModel):
    record_type: Literal["io.zzstoatzz.home.presence"] = Field(default=COLLECTION, alias="$type")
    state: Literal["home", "away", "unknown"]
    observedAt: AwareDatetime


class PresenceRecord(BaseModel):
    uri: str
    cid: str
    value: Presence


class Session(BaseModel):
    did: str
    accessJwt: str = Field(repr=False)


def read_presence(password: str) -> PresenceRecord:
    with httpx.Client(base_url=PDS, timeout=10) as client:
        authenticate_presence(client, password)
        response = client.get(
            "/xrpc/com.atproto.space.getRecord",
            params={"space": SPACE, "repo": OWNER, "collection": COLLECTION, "rkey": "self"},
        )
        response.raise_for_status()
        return PresenceRecord.model_validate(response.json())


def authenticate_presence(client: httpx.Client, password: str) -> None:
    response = client.post(
        "/xrpc/com.atproto.server.createSession",
        json={"identifier": OWNER, "password": password},
    )
    response.raise_for_status()
    session = Session.model_validate(response.json())
    if session.did != OWNER:
        raise ValueError("presence session belongs to another account")
    client.headers["Authorization"] = f"Bearer {session.accessJwt}"


def proposed_action(presence: Presence, now: datetime) -> str:
    age = (now - presence.observedAt).total_seconds()
    if age < 0 or presence.state == "unknown":
        return "NO_CHANGE"
    return "LIGHTS_OFF" if presence.state == "away" else "ARRIVAL_LOOK"


def apply_presence_update(
    client: httpx.Client, update: PresenceUpdate, now: datetime
) -> PresenceRecord:
    """Apply a report using an authenticated client, under the presence writer lock."""
    if update.observedAt > now:
        raise ValueError("presence observation is in the future")
    params = {"space": SPACE, "repo": OWNER, "collection": COLLECTION, "rkey": "self"}
    response = client.get("/xrpc/com.atproto.space.getRecord", params=params)
    response.raise_for_status()
    current = PresenceRecord.model_validate(response.json())
    if update.observedAt <= current.value.observedAt:
        return current
    record = Presence(state=update.state, observedAt=update.observedAt)
    response = client.post(
        "/xrpc/com.atproto.space.putRecord",
        json={**params, "record": record.model_dump(mode="json", by_alias=True)},
    )
    response.raise_for_status()
    response = client.get("/xrpc/com.atproto.space.getRecord", params=params)
    response.raise_for_status()
    observed = PresenceRecord.model_validate(response.json())
    if observed.value != record:
        raise ValueError("presence readback did not match the update")
    return observed
