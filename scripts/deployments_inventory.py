#!/usr/bin/env -S uv run --script --quiet
# /// script
# requires-python = ">=3.12"
# dependencies = ["pyyaml"]
# ///
"""Render docs/deployments.md from prefect.yaml.

The README used to carry the deployment list as hand-drawn ASCII, and it
drifted every time a schedule changed. This script reads the one source of
truth and writes the table; CI runs it with `--check` so the document cannot
be stale on main.

Each deployment must carry exactly one group tag (`tags: [pipeline]`), which
is how the table is sectioned. The purpose column is the deployment's
`description` when it has one, else the first paragraph of the flow
function's docstring, else the module docstring's.

usage:
    ./scripts/deployments_inventory.py           # rewrite docs/deployments.md
    ./scripts/deployments_inventory.py --check   # exit 1 if it would change
"""

import ast
import sys
from dataclasses import dataclass
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
SPEC = ROOT / "prefect.yaml"
OUT = ROOT / "docs" / "deployments.md"

GROUPS = {
    "pipeline": "the hub pipeline: ingest, classify, transform, brief",
    "phi": "phi's identity and memory",
    "publish": "snapshots and indexes published for other products",
    "gardener": "pi as a coding agent: diagnose, propose, revise, merge",
    "watch": "health, traffic, and cost reporting",
}


@dataclass(frozen=True)
class Deployment:
    name: str
    group: str
    cadence: str
    purpose: str
    entrypoint: str


class InventoryError(Exception):
    pass


def cadence(dep: dict) -> str:
    schedules = dep.get("schedules") or []
    triggers = dep.get("triggers") or []
    parts = []
    for s in schedules:
        cron = s.get("cron")
        if cron is None:
            raise InventoryError(f"{dep['name']}: only cron schedules are rendered")
        text = f"`{cron}`"
        if s.get("active") is False:
            text += " (inactive)"
        parts.append(text)
    for t in triggers:
        upstream = (t.get("match_related") or {}).get("prefect.resource.name")
        if not upstream:
            raise InventoryError(f"{dep['name']}: trigger without an upstream deployment")
        parts.append(f"after `{upstream}`")
    return ", ".join(parts) or "manual"


def first_paragraph(doc: str) -> str:
    lines = []
    for line in doc.strip().splitlines():
        if not line.strip():
            break
        lines.append(line.strip())
    return " ".join(lines).rstrip(".")


def purpose(dep: dict, root: Path) -> str:
    if dep.get("description"):
        return str(dep["description"]).strip().rstrip(".")
    entrypoint = dep["entrypoint"]
    path, fn = entrypoint.split(":")
    tree = ast.parse((root / path).read_text())
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == fn:
            doc = ast.get_docstring(node)
            if doc:
                return first_paragraph(doc)
    doc = ast.get_docstring(tree)
    if not doc:
        raise InventoryError(f"{entrypoint}: no docstring on the flow or its module")
    return first_paragraph(doc)


def group(dep: dict) -> str:
    tags = [t for t in dep.get("tags") or [] if t in GROUPS]
    if len(tags) != 1:
        raise InventoryError(
            f"{dep['name']}: needs exactly one group tag from {sorted(GROUPS)}, has {tags}"
        )
    return tags[0]


def load(spec: Path, root: Path) -> list[Deployment]:
    data = yaml.safe_load(spec.read_text())
    return [
        Deployment(
            name=d["name"],
            group=group(d),
            cadence=cadence(d),
            purpose=purpose(d, root),
            entrypoint=d["entrypoint"],
        )
        for d in data["deployments"]
    ]


def render(deps: list[Deployment]) -> str:
    lines = [
        "# deployments",
        "",
        "generated from `prefect.yaml` by `scripts/deployments_inventory.py`; "
        "do not edit by hand. `just inventory` regenerates it and CI fails on drift.",
        "",
        f"{len(deps)} deployments, all on the `home-pool` process worker. "
        "a cadence of `after x` is an automation that fires when deployment x completes; "
        "`manual` means the deployment is started by the API, an automation outside "
        "`prefect.yaml`, or a person.",
        "",
    ]
    for g, blurb in GROUPS.items():
        members = [d for d in deps if d.group == g]
        if not members:
            continue
        lines += [
            f"## {g}",
            "",
            blurb,
            "",
            "| deployment | cadence | purpose | entrypoint |",
            "|---|---|---|---|",
        ]
        for d in members:
            path, fn = d.entrypoint.split(":")
            lines.append(f"| `{d.name}` | {d.cadence} | {d.purpose} | [`{fn}`]({path}) |")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def main(argv: list[str]) -> int:
    check = "--check" in argv
    text = render(load(SPEC, ROOT))
    current = OUT.read_text() if OUT.exists() else ""
    if check:
        if text != current:
            print(f"{OUT.relative_to(ROOT)} is stale; run `just inventory`", file=sys.stderr)
            return 1
        return 0
    OUT.write_text(text)
    print(f"wrote {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except InventoryError as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(2)
