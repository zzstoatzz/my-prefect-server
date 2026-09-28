#!/usr/bin/env -S uv run --script --quiet
# /// script
# requires-python = ">=3.12"
# dependencies = ["pyyaml"]
# ///
"""Render docs/deployments.md from the repository's deployment specs.

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
SPECS = [ROOT / "prefect.yaml", ROOT / "deploy" / "presence.yaml"]
OUT = ROOT / "docs" / "deployments.md"

GROUPS = {
    "pipeline": "the hub pipeline: ingest, classify, transform, brief",
    "phi": "phi's identity and memory",
    "publish": "snapshots and indexes published for other products",
    "gardener": "pi as a coding agent: diagnose, propose, revise, merge",
    "watch": "health, traffic, and cost reporting",
    "home": "the house: phone presence and lighting",
}


@dataclass(frozen=True)
class Deployment:
    name: str
    group: str
    cadence: str
    purpose: str
    entrypoint: str
    pool: str = "home-pool"
    spec: str = "prefect.yaml"


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
        if upstream:
            parts.append(f"after `{upstream}`")
            continue
        events = t.get("expect") or []
        if not events:
            raise InventoryError(f"{dep['name']}: trigger without an upstream deployment or event")
        text = ", ".join(f"on `{e}`" for e in events)
        parts.append(text + (" (disabled)" if t.get("enabled") is False else ""))
    return ", ".join(parts) or "manual"


def first_paragraph(doc: str) -> str:
    lines = []
    for line in doc.strip().splitlines():
        if not line.strip():
            break
        lines.append(line.strip())
    return " ".join(lines).rstrip(".")


def entrypoint_source(entrypoint: str) -> tuple[str, str]:
    if ":" in entrypoint:
        path, fn = entrypoint.rsplit(":", 1)
        return path, fn
    module, fn = entrypoint.rsplit(".", 1)
    return module.replace(".", "/") + ".py", fn


def purpose(dep: dict, root: Path) -> str:
    if dep.get("description"):
        return str(dep["description"]).strip().rstrip(".")
    entrypoint = dep["entrypoint"]
    path, fn = entrypoint_source(entrypoint)
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


# Deployments on the server that no spec in this repository declares.
REGISTERED_ELSEWHERE = {
    "mcp-atlas": "registered by the mcp-atlas repository, which owns its schedule and code",
}


def load(specs: list[Path], root: Path) -> list[Deployment]:
    deps = []
    for spec in specs:
        data = yaml.safe_load(spec.read_text())
        for d in data["deployments"]:
            deps.append(
                Deployment(
                    name=d["name"],
                    group=group(d),
                    cadence=cadence(d),
                    purpose=purpose(d, root),
                    entrypoint=d["entrypoint"],
                    pool=(d.get("work_pool") or {}).get("name") or "unset",
                    spec=str(spec.relative_to(root)),
                )
            )
    return deps


def pool_summary(deps: list[Deployment]) -> str:
    counts: dict[str, int] = {}
    for d in deps:
        counts[d.pool] = counts.get(d.pool, 0) + 1
    ordered = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return ", ".join(f"{n} on `{pool}`" for pool, n in ordered)


def render(deps: list[Deployment]) -> str:
    specs = sorted({d.spec for d in deps}, key=lambda s: (s != "prefect.yaml", s))
    lines = [
        "# deployments",
        "",
        "generated from "
        + " and ".join(f"`{s}`" for s in specs)
        + " by `scripts/deployments_inventory.py`; "
        "do not edit by hand. `just inventory` regenerates it and CI fails on drift.",
        "",
        f"{len(deps)} deployments: {pool_summary(deps)}. "
        "a cadence of `after x` is an automation that fires when deployment x completes; "
        "`manual` means the deployment is started by the API, an automation outside "
        "its spec, or a person.",
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
            path, fn = entrypoint_source(d.entrypoint)
            lines.append(f"| `{d.name}` | {d.cadence} | {d.purpose} | [`{fn}`]({path}) |")
        lines.append("")
    lines += ["## registered elsewhere", "", "on the server, but declared by no spec here.", ""]
    lines += [f"- `{name}`: {where}" for name, where in REGISTERED_ELSEWHERE.items()]
    return "\n".join(lines).rstrip() + "\n"


def main(argv: list[str]) -> int:
    check = "--check" in argv
    text = render(load(SPECS, ROOT))
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
