import json
from pathlib import Path

from mps.pi_skills import configure_skills


def test_no_selected_skills_does_not_load_an_implicit_profile(tmp_path):
    configure_skills(tmp_path, [])
    config = json.loads((tmp_path / ".pi/agent/mcp-adapter.json").read_text())
    assert config["mcpServers"] == {}


def test_explicit_skill_keeps_its_supporting_files_and_no_implicit_profile(tmp_path):
    source = tmp_path / "selected"
    source.mkdir()
    (source / "SKILL.md").write_text("---\nname: selected\ndescription: Selected skill.\n---\n")
    (source / "references").mkdir()
    (source / "references/details.md").write_text("Supporting material.\n")
    home = tmp_path / "home"
    configure_skills(home, [str(source)])
    config = json.loads((home / ".pi/agent/mcp-adapter.json").read_text())
    (server,) = config["mcpServers"].values()
    assert server["args"] == ["--directory", "/home/agent/skill-sources/0"]
    copied = home / Path(server["args"][1]).relative_to("/home/agent")
    assert (copied / "SKILL.md").read_bytes() == (source / "SKILL.md").read_bytes()
    assert (copied / "references/details.md").read_text() == "Supporting material.\n"
