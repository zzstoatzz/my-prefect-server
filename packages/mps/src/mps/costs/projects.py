"""Explicit resource ownership for cost attribution.

Resource names must be declared here. Unknown resources remain unattributed;
substring resemblance is never evidence of ownership.
"""

UNATTRIBUTED = "unattributed"

RESOURCE_DECLARATIONS: dict[str, tuple[str, ...]] = {
    "bufo": ("bufo-bot",),
    "labelz": ("labelz",),
    "misc": (
        "bsky-feed",
        "pollz-backend",
        "zig-bsky-feed",
    ),
    "pds-infra": (
        "pds-zzstoatzz-io",
        "zds-pds",
    ),
    "phi": ("zzstoatzz-phi",),
    "plyr.fm": (
        "audio-dev",
        "audio-private-dev",
        "audio-private-prod",
        "audio-private-staging",
        "audio-prod",
        "audio-staging",
        "images-dev",
        "images-prod",
        "images-staging",
        "plyr",
        "plyr-dev",
        "plyr-moderation",
        "plyr-prd",
        "plyr-radio",
        "plyr-radio-preview",
        "plyr-redis",
        "plyr-redis-stg",
        "plyr-stats",
        "plyr-stg",
        "plyr-transcoder",
        "plyr.fm",
        "relay-api",
        "relay-api-staging",
    ),
    "prefect": ("prefect-server",),
    "relays": (
        "relay",
        "relay-eval",
        "relay.waow",
        "zlay",
    ),
    "standard.site": (
        "leaflet-search-backend",
        "leaflet-search-tap",
    ),
    "status": ("zzstoatzz-quickslice-status",),
    "stream": (
        "stream-20260728-0501-archive",
        "stream-cx43",
    ),
    "trending": ("coral",),
    "typeahead": (
        "typeahead-ingester",
        "typeahead-search",
    ),
}


def resource_owners(declarations: dict[str, tuple[str, ...]]) -> dict[str, str]:
    owners = {}
    for project, resources in declarations.items():
        for resource in resources:
            name = resource.casefold()
            if name in owners and owners[name] != project:
                raise ValueError(f"conflicting cost ownership for {resource}")
            owners[name] = project
    return owners


_RESOURCE_OWNERS = resource_owners(RESOURCE_DECLARATIONS)


def project_for(resource: str) -> str:
    return _RESOURCE_OWNERS.get(resource.partition(":")[0].casefold(), UNATTRIBUTED)
