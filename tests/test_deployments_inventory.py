import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location(
    "deployments_inventory", ROOT / "scripts" / "deployments_inventory.py"
)
inv = importlib.util.module_from_spec(spec)
spec.loader.exec_module(inv)


def write_flow(root: Path, name: str, body: str) -> None:
    (root / "flows").mkdir(exist_ok=True)
    (root / "flows" / f"{name}.py").write_text(body)


def test_cadence_renders_cron_trigger_and_manual():
    assert inv.cadence({"name": "a", "schedules": [{"cron": "0 * * * *"}]}) == "`0 * * * *`"
    assert (
        inv.cadence({"name": "a", "schedules": [{"cron": "0 * * * *", "active": False}]})
        == "`0 * * * *` (inactive)"
    )
    assert (
        inv.cadence(
            {
                "name": "a",
                "triggers": [{"match_related": {"prefect.resource.name": "ingest"}}],
            }
        )
        == "after `ingest`"
    )
    assert (
        inv.cadence({"name": "a", "triggers": [{"expect": ["hub.brief.ready"]}]})
        == "on `hub.brief.ready`"
    )
    assert (
        inv.cadence({"name": "a", "triggers": [{"expect": ["x"], "enabled": False}]})
        == "on `x` (disabled)"
    )
    with pytest.raises(inv.InventoryError):
        inv.cadence({"name": "a", "triggers": [{"expect": []}]})
    assert inv.cadence({"name": "a"}) == "manual"


def test_purpose_prefers_description_then_flow_docstring_then_module(tmp_path: Path):
    write_flow(
        tmp_path,
        "one",
        '"""module line."""\ndef one():\n    """flow line\n    wraps here.\n\n    details.\n    """\n',
    )
    write_flow(tmp_path, "two", '"""module line."""\ndef two():\n    pass\n')
    write_flow(tmp_path, "three", "def three():\n    pass\n")
    assert (
        inv.purpose({"entrypoint": "flows/one.py:one", "description": "declared."}, tmp_path)
        == "declared"
    )
    assert inv.purpose({"entrypoint": "flows/one.py:one"}, tmp_path) == "flow line wraps here"
    assert inv.purpose({"entrypoint": "flows.one.one"}, tmp_path) == "flow line wraps here"
    assert inv.purpose({"entrypoint": "flows/two.py:two"}, tmp_path) == "module line"
    with pytest.raises(inv.InventoryError):
        inv.purpose({"entrypoint": "flows/three.py:three"}, tmp_path)


def test_group_requires_exactly_one_group_tag():
    assert inv.group({"name": "a", "tags": ["phi", "other"]}) == "phi"
    with pytest.raises(inv.InventoryError):
        inv.group({"name": "a", "tags": []})
    with pytest.raises(inv.InventoryError):
        inv.group({"name": "a", "tags": ["phi", "watch"]})


def test_render_sections_by_group_in_declared_order():
    deps = [
        inv.Deployment("w", "watch", "manual", "watches", "flows.w.w"),
        inv.Deployment("p", "pipeline", "`0 * * * *`", "ingests", "flows/p.py:p"),
    ]
    text = inv.render(deps)
    assert text.index("## pipeline") < text.index("## watch")
    assert "| `p` | `0 * * * *` | ingests | [`p`](flows/p.py) |" in text
    assert "## phi" not in text
    assert "[`w`](flows/w.py)" in text


def test_committed_inventory_matches_prefect_yaml():
    deps = inv.load(inv.SPECS, inv.ROOT)
    assert inv.render(deps) == inv.OUT.read_text()


def test_render_counts_pools_and_names_deployments_declared_elsewhere():
    deps = [
        inv.Deployment("a", "watch", "manual", "a", "flows/a.py:a", pool="home-pool"),
        inv.Deployment("b", "watch", "manual", "b", "flows/b.py:b", pool="home-pool"),
        inv.Deployment(
            "c", "home", "manual", "c", "flows/c.py:c", pool="laptop-pool", spec="deploy/c.yaml"
        ),
    ]
    text = inv.render(deps)
    assert "3 deployments: 2 on `home-pool`, 1 on `laptop-pool`." in text
    assert "generated from `prefect.yaml` and `deploy/c.yaml`" in text
    assert "## registered elsewhere" in text and "`mcp-atlas`" in text


def test_every_in_repo_spec_is_inventoried():
    names = {d.name for d in inv.load(inv.SPECS, inv.ROOT)}
    assert "phone-presence" in names
    assert not names & set(inv.REGISTERED_ELSEWHERE)
