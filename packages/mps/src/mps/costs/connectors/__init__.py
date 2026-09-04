"""Connector registry. Add a provider here and the flow picks it up."""

from __future__ import annotations

from mps.costs.connectors.cloudflare import CloudflareConnector
from mps.costs.connectors.fly import FlyConnector
from mps.costs.connectors.hetzner import HetznerConnector
from mps.costs.connectors.neon import NeonConnector
from mps.costs.types import Connector

ALL_CONNECTORS: list[Connector] = [
    FlyConnector(),
    CloudflareConnector(),
    HetznerConnector(),
    NeonConnector(),
]

__all__ = [
    "ALL_CONNECTORS",
    "CloudflareConnector",
    "FlyConnector",
    "HetznerConnector",
    "NeonConnector",
]
