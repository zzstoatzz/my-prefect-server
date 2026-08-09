"""Maps a provider resource (fly app, hetzner server, etc.) to a project slug.

Project slugs intentionally match the groups on the evergreen status page so a
single taxonomy spans both status and cost. Matching is longest-substring-wins
against the resource name; unmatched resources fall to "unattributed" so nothing
silently vanishes from the total.
"""

from __future__ import annotations

# substring -> project slug. order doesn't matter; the longest matching
# substring wins so specific names (plyr-transcoder) beat generic ones (plyr).
_RESOURCE_PATTERNS: dict[str, str] = {
    # plyr.fm
    "plyr": "plyr.fm",
    "relay-api": "plyr.fm",  # plyr backend is historically named relay-api
    "moderation": "plyr.fm",
    "transcoder": "plyr.fm",
    "audd": "plyr.fm",
    "plyr.fm": "plyr.fm",
    "audio-prod": "plyr.fm",
    "audio-staging": "plyr.fm",
    "audio-dev": "plyr.fm",
    "audio-private-prod": "plyr.fm",
    "audio-private-staging": "plyr.fm",
    "audio-private-dev": "plyr.fm",
    "images-prod": "plyr.fm",
    "images-staging": "plyr.fm",
    "images-dev": "plyr.fm",
    "plyr-stats": "plyr.fm",
    # typeahead
    "typeahead": "typeahead",
    # standard.site index
    "leaflet-search": "standard.site",
    "pub-search": "standard.site",
    # relays (the atproto relays, distinct from plyr's relay-api). bare "relay"
    # is the relay host; "relay-api"/"relay-api-staging" win by longer match.
    "relay": "relays",
    "relay.waow": "relays",
    "zlay": "relays",
    "relay-eval": "relays",
    # stream (tangled.org/zat.dev/stream — jetstream + archive, hetzner hel1)
    "stream": "stream",
    # trending topics
    "coral": "trending",
    # bufo
    "bufo": "bufo",
    "find-bufo": "bufo",
    # phi
    "phi": "phi",
    # prefect infra
    "prefect": "prefect",
    # pds infra
    "pds": "pds-infra",
    # labelz
    "labelz": "labelz",
    # misc
    "pollz": "misc",
    "bsky-feed": "misc",  # zig-bsky-feed and bsky-feed
    "status": "status",  # zzstoatzz-quickslice-status etc.
}

UNATTRIBUTED = "unattributed"


def project_for(resource: str) -> str:
    """Return the project slug for a resource name (case-insensitive)."""
    name = resource.lower()
    best: tuple[int, str] | None = None
    for pattern, project in _RESOURCE_PATTERNS.items():
        if pattern in name and (best is None or len(pattern) > best[0]):
            best = (len(pattern), project)
    return best[1] if best else UNATTRIBUTED
