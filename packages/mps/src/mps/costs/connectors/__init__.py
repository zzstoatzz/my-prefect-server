"""Connector registry. Add a provider here and the flow picks it up."""

from __future__ import annotations

from mps.costs.connectors.builder import BuilderCostsConnector
from mps.costs.connectors.cloudflare import CloudflareConnector
from mps.costs.connectors.coral_api import CoralApiConnector
from mps.costs.connectors.fly import FlyConnector
from mps.costs.connectors.hetzner import HetznerConnector
from mps.costs.connectors.neon import NeonConnector
from mps.costs.connectors.openrouter import OpenRouterConnector
from mps.costs.connectors.turso import TursoConnector
from mps.costs.types import Connector

ALL_CONNECTORS: list[Connector] = [
    FlyConnector(),
    CloudflareConnector(),
    HetznerConnector(),
    NeonConnector(),
    TursoConnector(),
    CoralApiConnector(),
    OpenRouterConnector(),
    BuilderCostsConnector(),
]

__all__ = [
    "ALL_CONNECTORS",
    "BuilderCostsConnector",
    "CloudflareConnector",
    "CoralApiConnector",
    "FlyConnector",
    "HetznerConnector",
    "NeonConnector",
    "OpenRouterConnector",
    "TursoConnector",
]
