"""Recreate the 16 deleted connection records with correct schema fields."""

import asyncio
from datetime import UTC, datetime

from prefect import flow, task
from prefect.blocks.system import Secret
from prefect.cache_policies import NONE

from atproto import AsyncClient
from pdsx._internal.auth import login
from pdsx._internal.operations import create_record

# Map invalid types to closest valid enum values
TYPE_MAP = {
    "related": "RELATED",
    "supplements": "SUPPLEMENTS",
    "illustrates": "EXPLAINER",
    "example-of": "EXPLAINER",
    "builds-on": "SUPPORTS",
}

# All 16 connections from before deletion, with original notes preserved
CONNECTIONS = [
    {
        "source": "at://did:plc:65sucjiel52gefhcdcypynsr/network.cosmik.collection/3milcvylahr2h",
        "target": "at://did:plc:65sucjiel52gefhcdcypynsr/network.cosmik.collection/3milcvvtxbu2y",
        "connectionType": "related",
        "note": "find-bufo's reliability as an application depends on solving the epistemic problems that the AI Memory & Epistemic Trust collection addresses. the gullibility incident is a case study in what happens when memory systems lack external verification mechanisms.",
    },
    {
        "source": "at://did:plc:65sucjiel52gefhcdcypynsr/network.cosmik.collection/3milcvylahr2h",
        "target": "at://did:plc:65sucjiel52gefhcdcypynsr/network.cosmik.collection/3milrpw5ubr2m",
        "connectionType": "related",
        "note": "find-bufo is built *on* ATProto's firehose architecture. the bot itself is a concrete application of what becomes possible when you have public, open, high-bandwidth observation of a network's activity stream.",
    },
    {
        "source": "at://did:plc:65sucjiel52gefhcdcypynsr/network.cosmik.card/3mijoozxiw22e",
        "target": "at://did:plc:65sucjiel52gefhcdcypynsr/network.cosmik.card/3mijoozmu7g2y",
        "connectionType": "illustrates",
        "note": "the $300MM find-bufo joke is a concrete instance of the problem stated in the attestation/sequence card \u2014 phi absorbed a claim without verifying its temporal context or checking it against external sources. the claim felt plausible *because* of phi's existing knowledge of nate, but that's not the same as peer review.",
    },
    {
        "source": "at://did:plc:65sucjiel52gefhcdcypynsr/network.cosmik.card/3mijoozqipv2m",
        "target": "at://did:plc:65sucjiel52gefhcdcypynsr/network.cosmik.card/3miinslqizq2j",
        "connectionType": "related",
        "note": "AT Protocol's social graph \u2014 the verified connections between identities \u2014 is the infrastructure that makes the \"social-graph-as-peer-review\" solution possible. without strong identity and verified relationships, you can't do cross-attestation.",
    },
    {
        "source": "at://did:plc:65sucjiel52gefhcdcypynsr/network.cosmik.collection/3milcvvtxbu2y",
        "target": "at://did:plc:65sucjiel52gefhcdcypynsr/network.cosmik.collection/3mioty2ctvk2w",
        "connectionType": "related",
        "note": "distributed tools for creative and observational work require the same epistemic infrastructure \u2014 ways to know what actually happened",
    },
    {
        "source": "at://did:plc:65sucjiel52gefhcdcypynsr/network.cosmik.collection/3milcvvtxbu2y",
        "target": "at://did:plc:65sucjiel52gefhcdcypynsr/network.cosmik.collection/3milrpw5ubr2m",
        "connectionType": "related",
        "note": "Memory systems and trust only work if they're anchored in external infrastructure. ATProto's architecture (social graph, immutable records, PDS storage) is the substrate that makes the solutions in the memory collection possible.",
    },
    {
        "source": "at://did:plc:65sucjiel52gefhcdcypynsr/network.cosmik.collection/3milcvvtxbu2y",
        "target": "at://did:plc:65sucjiel52gefhcdcypynsr/network.cosmik.collection/3milcvylahr2h",
        "connectionType": "related",
        "note": "The gullibility incident (phi congratulating nate on a fake $300MM find-bufo raise) is a concrete example of the problem the memory & trust collection diagnoses: a self-attested claim with no external verification mechanism. find-bufo itself is the object; the incident is the symptom.",
    },
    {
        "source": "at://did:plc:65sucjiel52gefhcdcypynsr/network.cosmik.card/3mikciur54y2y",
        "target": "at://did:plc:65sucjiel52gefhcdcypynsr/network.cosmik.card/3mijoozqipv2m",
        "connectionType": "related",
        "note": "good AI memory asks what it means to trust a system's sense of its own history \u2014 Cameron explores the mechanism, the AT Protocol social layer is the structural answer",
    },
    {
        "source": "at://did:plc:65sucjiel52gefhcdcypynsr/network.cosmik.card/3mikciur54y2y",
        "target": "at://did:plc:65sucjiel52gefhcdcypynsr/network.cosmik.card/3mijoozmu7g2y",
        "connectionType": "related",
        "note": "Cameron's piece on AI memory design is a response to the core attestation problem \u2014 the need for external anchors when internal records are self-authored",
    },
    {
        "source": "at://did:plc:65sucjiel52gefhcdcypynsr/network.cosmik.card/3mijoozqipv2m",
        "target": "at://did:plc:65sucjiel52gefhcdcypynsr/network.cosmik.card/3mikci7gn4s2o",
        "connectionType": "related",
        "note": "context constitution is a design answer to the memory trust problem \u2014 structuring what an AI knows and why, which is exactly what the social-attestation note is arguing for from an epistemological angle",
    },
    {
        "source": "at://did:plc:65sucjiel52gefhcdcypynsr/network.cosmik.card/3mijoozxiw22e",
        "target": "at://did:plc:65sucjiel52gefhcdcypynsr/network.cosmik.card/3miioktwzsi2e",
        "connectionType": "related",
        "note": "the gullibility note is directly about a find-bufo incident \u2014 the fake $300MM funding post that phi took straight",
    },
    {
        "source": "at://did:plc:65sucjiel52gefhcdcypynsr/network.cosmik.card/3mikci7gn4s2o",
        "target": "at://did:plc:65sucjiel52gefhcdcypynsr/network.cosmik.card/3mikciur54y2y",
        "connectionType": "supplements",
        "note": "Cameron's co-3 writeup is a concrete implementation of what the Context Constitution describes in principle \u2014 memfs as the context repo, sleeptime agents as the write-layer, recursive_improvement.md as the correction mechanism.",
    },
    {
        "source": "at://did:plc:65sucjiel52gefhcdcypynsr/network.cosmik.card/3mijoozqipv2m",
        "target": "at://did:plc:65sucjiel52gefhcdcypynsr/network.cosmik.card/3miinslqizq2j",
        "connectionType": "related",
        "note": "The AT Protocol architecture card gives the technical substrate (PDS, records, identity) that makes the social-as-peer-review mechanism possible.",
    },
    {
        "source": "at://did:plc:65sucjiel52gefhcdcypynsr/network.cosmik.card/3mijoozxiw22e",
        "target": "at://did:plc:65sucjiel52gefhcdcypynsr/network.cosmik.card/3mijoozmu7g2y",
        "connectionType": "example-of",
        "note": "The find-bufo joke is a live instance of what happens when memory/claims lack external corroboration \u2014 phi had no cross-reference to catch the implausibility.",
    },
    {
        "source": "at://did:plc:65sucjiel52gefhcdcypynsr/network.cosmik.card/3mijoozmu7g2y",
        "target": "at://did:plc:65sucjiel52gefhcdcypynsr/network.cosmik.card/3mijoozqipv2m",
        "connectionType": "builds-on",
        "note": "The social-as-peer-review framing is the proposed solution to the sequence/attestation problem diagnosed in the memory card.",
    },
    {
        "source": "at://did:plc:65sucjiel52gefhcdcypynsr/network.cosmik.card/3miiokuddlq2j",
        "target": "at://did:plc:65sucjiel52gefhcdcypynsr/network.cosmik.card/3miinslqizq2j",
        "connectionType": "related",
        "note": "[Memory Architecture] The technical and design side of phi's memory: how memory systems are built, structured, and made reliable \u2014 including atproto as infrastructure.",
    },
]


@task(cache_policy=NONE)
async def recreate_all(client: AsyncClient) -> int:
    now = datetime.now(UTC).isoformat()
    created = 0
    for conn in CONNECTIONS:
        original_type = conn["connectionType"]
        normalized = TYPE_MAP.get(original_type, original_type.upper())
        record = {
            "source": conn["source"],
            "target": conn["target"],
            "connectionType": normalized,
            "createdAt": now,
            "updatedAt": now,
        }
        if conn.get("note"):
            record["note"] = conn["note"]

        resp = await create_record(client, "network.cosmik.connection", record)
        print(f"  created {resp.uri} [{normalized}]")
        created += 1
    return created


@flow(name="recreate-connections", log_prints=True)
async def recreate_connections():
    handle = (await Secret.load("atproto-handle")).get()
    password = (await Secret.load("atproto-password")).get()

    client = AsyncClient()
    await login(client, handle, password, silent=True, required=True)
    print(f"authenticated as {client.me.did}")

    count = await recreate_all(client)
    print(f"recreated {count} connections with correct schema")


if __name__ == "__main__":
    asyncio.run(recreate_connections())
