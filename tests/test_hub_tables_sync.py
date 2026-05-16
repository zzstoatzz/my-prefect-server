"""Enforce that flows/transform.py:HUB_TABLES covers every table the hub
SvelteKit server actually queries.

The hub mounts a slim duckdb (hub.duckdb) built by `export_hub_db`; that file
only contains the tables listed in HUB_TABLES. If a hub loader gains a `FROM
some_new_table` and HUB_TABLES isn't updated in the same PR, hub will 500 in
prod the next time that endpoint is hit. This test catches the drift at PR
time instead.

Strategy: scan every .ts file under web/src/lib/server/ for SQL `FROM <ident>`
literals and assert each one is in HUB_TABLES. We deliberately don't try to
parse SQL — a regex over the source files is cheap, predictable, and easy to
debug when it fails.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from flows.transform import HUB_TABLES

REPO_ROOT = Path(__file__).resolve().parent.parent
HUB_SERVER_DIR = REPO_ROOT / "web" / "src" / "lib" / "server"

# `FROM <ident>` where ident is a bare table name (letters/digits/underscores).
# Uppercase-only so we don't match prose like "as it comes from the DB" inside
# JSDoc comments. Hub's loaders use uppercase SQL keywords by convention; if
# that changes, strip comments before scanning instead.
_FROM_RE = re.compile(r"\bFROM\s+([a-z_][a-z0-9_]*)\b")

# Strip JS/TS line + block comments before scanning. Comments shouldn't
# influence table extraction; without this we hit false positives on prose
# like "row as it comes back from the DB".
_LINE_COMMENT_RE = re.compile(r"//[^\n]*")
_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)

# SQL keywords / clause heads that can syntactically follow FROM in a way our
# regex would mis-capture. None today, but listed for explicitness if a future
# query does e.g. `FROM (SELECT ...)`.
_NOT_TABLE_NAMES = frozenset({"select"})


def _strip_comments(text: str) -> str:
    text = _BLOCK_COMMENT_RE.sub("", text)
    text = _LINE_COMMENT_RE.sub("", text)
    return text


def _tables_referenced_in_hub_loaders() -> set[str]:
    """Return every bare table name appearing after `FROM` in any hub .ts file."""
    if not HUB_SERVER_DIR.exists():
        pytest.fail(f"hub server dir not found: {HUB_SERVER_DIR}")

    found: set[str] = set()
    for ts_file in HUB_SERVER_DIR.rglob("*.ts"):
        text = _strip_comments(ts_file.read_text())
        for match in _FROM_RE.finditer(text):
            name = match.group(1).lower()
            if name in _NOT_TABLE_NAMES:
                continue
            found.add(name)

    return found


def test_hub_tables_covers_every_table_queried_by_hub_loaders() -> None:
    """HUB_TABLES must be a superset of what the hub server queries.

    Failing this means hub.duckdb won't contain a table that a hub endpoint
    expects → 500 in prod. Fix by adding the new table to HUB_TABLES in
    flows/transform.py (and verifying it exists in analytics.duckdb).
    """
    queried = _tables_referenced_in_hub_loaders()
    declared = set(HUB_TABLES)

    missing = queried - declared
    assert not missing, (
        f"hub loaders reference {sorted(missing)} but HUB_TABLES doesn't "
        f"include them. add to flows/transform.py:HUB_TABLES so the slim "
        f"hub.duckdb contains them. (queried={sorted(queried)}, "
        f"declared={sorted(declared)})"
    )


def test_hub_tables_has_no_unused_entries() -> None:
    """HUB_TABLES shouldn't carry tables no hub loader queries.

    Soft signal — failing means hub.duckdb is bigger than it needs to be,
    or the loader using a table got removed but HUB_TABLES wasn't pruned.
    Not load-bearing for correctness; remove the unused entry from
    flows/transform.py:HUB_TABLES.
    """
    queried = _tables_referenced_in_hub_loaders()
    declared = set(HUB_TABLES)

    unused = declared - queried
    assert not unused, (
        f"HUB_TABLES declares {sorted(unused)} but no hub loader uses them. "
        f"prune from flows/transform.py:HUB_TABLES to keep hub.duckdb slim."
    )
