"""Typed access to Prefect Secret blocks from flow code.

`Secret.load` is async-dispatched: it returns a block in sync code and a
coroutine in async code, and its annotation is the union of both. These
wrappers choose the path explicitly so a caller gets a `str` or a mapping and
never something it might have to await.
"""

import json
from typing import Any

from prefect.blocks.system import Secret
from prefect.utilities.asyncutils import run_coro_as_sync


async def secret(name: str) -> str:
    return _text(name, (await Secret.aload(name)).get())


def secret_sync(name: str) -> str:
    return _text(name, run_coro_as_sync(Secret.aload(name)).get())


async def secret_mapping(name: str) -> dict[str, Any]:
    """A secret whose value is a JSON object, stored either as a dict or as JSON text."""
    return _mapping(name, (await Secret.aload(name)).get())


def secret_mapping_sync(name: str) -> dict[str, Any]:
    return _mapping(name, run_coro_as_sync(Secret.aload(name)).get())


def _text(name: str, value: object) -> str:
    if not isinstance(value, str):
        raise TypeError(f"secret {name!r} holds {type(value).__name__}, expected str")
    return value


def _mapping(name: str, value: object) -> dict[str, Any]:
    if isinstance(value, dict) and set(value) == {"value"}:
        value = next(iter(value.values()))
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, dict):
        raise TypeError(f"secret {name!r} holds {type(value).__name__}, expected a JSON object")
    return {str(k): v for k, v in value.items()}
