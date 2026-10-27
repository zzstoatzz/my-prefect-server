"""Resolve an explicit operator target without changing global client profiles."""

import os
import tomllib
from pathlib import Path


def target_config():
    path = Path(__file__).resolve().parent.parent / "deploy/targets.toml"
    config = tomllib.loads(path.read_text())
    name = os.environ.get("PREFECT_TARGET", config["default_target"])
    if name not in config["targets"]:
        raise ValueError("Unknown Prefect target: " + name)
    return config["targets"][name]
