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
    assert inv.cadence({"name": "a"}) == "manual"


def test_purpose_prefers_description_then_flow_docstring_then_module(tmp_path: Path):
    write_flow(tmp_path, "one", '"""module line."""\ndef one():\n    """flow line\n    wraps here.\n\n    details.\n    """\n')
    write_flow(tmp_path, "two", '"""module line."""\ndef two():\n    pass\n')
    write_flow(tmp_path, "three", "def three():\n    pass\n")
    assert inv.purpose({"entrypoint": "flows/one.py:one", "description": "declared."}, tmp_path) == "declared"
    assert inv.purpose({"entrypoint": "flows/one.py:one"}, tmp_path) == "flow line wraps here"
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
        inv.Deployment("w", "watch", "manual", "watches", "flows/w.py:w"),
        inv.Deployment("p", "pipeline", "`0 * * * *`", "ingests", "flows/p.py:p"),
    ]
    text = inv.render(deps)
    assert text.index("## pipeline") < text.index("## watch")
    assert "| `p` | `0 * * * *` | ingests | [`p`](flows/p.py) |" in text
    assert "## phi" not in text


def test_committed_inventory_matches_prefect_yaml():
    deps = inv.load(inv.SPEC, inv.ROOT)
    assert inv.render(deps) == inv.OUT.read_text()
