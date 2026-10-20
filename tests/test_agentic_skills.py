from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
LOADER = ROOT / "scripts/load_fastmcp_skills.sh"
MANIFEST = ROOT / "deploy/agentic-workflows/skills.json"


def git(repository: Path, *arguments: str) -> str:
    return subprocess.check_output(["git", "-C", str(repository), *arguments], text=True).strip()


@pytest.fixture
def repository(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-b", "main")
    git(repo, "config", "user.name", "Skill test")
    git(repo, "config", "user.email", "skills@example.invalid")
    paths = subprocess.check_output(
        ["jq", "-r", "[.common[], .roles[][]] | unique[]", str(MANIFEST)], text=True
    ).splitlines()
    for path in paths:
        file = repo / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(f"# {path}\nLiteral $(no-command), `backticks`, λ.\n\n")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "Initial instructions")
    return repo, git(repo, "rev-parse", "HEAD")


def load(repo: Path, revision: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(LOADER), revision, str(repo)], text=True, capture_output=True, check=False
    )


def test_loads_exact_pinned_content_and_stage_specific_skills(repository, tmp_path):
    repo, base = repository
    (repo / "AGENTS.md").write_text("New policy that must not leak into the old revision\n")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "Update policy")
    result = load(repo, base)
    assert result.returncode == 0, result.stderr
    bundle = tmp_path / "bundle.json"
    bundle.write_text(result.stdout)
    subprocess.run(
        [
            "jq",
            "-e",
            "--arg",
            "base",
            base,
            "--slurpfile",
            "m",
            str(MANIFEST),
            """all(.[]; .base_sha == $base and .repository == "PrefectHQ/fastmcp") and
            (keys == ["eligibility_instructions", "fix_instructions", "review_instructions"]) and
            ([to_entries[] | .key as $key |
              (.value.files | keys) == ([$m[0].common[],
                $m[0].roles[($key | rtrimstr("_instructions"))][]] | unique)] | all)""",
            str(bundle),
        ],
        check=True,
        capture_output=True,
    )
    for role in ("eligibility", "fix", "review"):
        paths = subprocess.check_output(
            ["jq", "-r", f".{role}_instructions.files | keys[]", str(bundle)], text=True
        ).splitlines()
        for path in paths:
            original = subprocess.check_output(["git", "-C", str(repo), "show", f"{base}:{path}"])
            loaded = subprocess.check_output(
                [
                    "jq",
                    "-j",
                    "--arg",
                    "path",
                    path,
                    f".{role}_instructions.files[$path].content",
                    str(bundle),
                ]
            )
            digest = subprocess.check_output(
                [
                    "jq",
                    "-r",
                    "--arg",
                    "path",
                    path,
                    f".{role}_instructions.files[$path].sha256",
                    str(bundle),
                ],
                text=True,
            ).strip()
            assert loaded == original
            assert digest == hashlib.sha256(original).hexdigest()


@pytest.mark.parametrize("failure", ["missing", "empty", "symlink", "oversized", "reference"])
def test_missing_or_invalid_required_skill_stops_setup(repository, failure):
    repo, _ = repository
    path = (
        "docs/development/contributing.mdx"
        if failure == "reference"
        else ".agents/skills/fix-issue/SKILL.md"
    )
    skill = repo / path
    skill.unlink()
    if failure == "empty":
        skill.touch()
    elif failure == "symlink":
        skill.symlink_to("../../../AGENTS.md")
    elif failure == "oversized":
        skill.write_text("x" * 100001)
    git(repo, "add", ".")
    git(repo, "commit", "-m", "Invalid skill")
    result = load(repo, "main")
    assert result.returncode != 0
    assert result.stdout == ""
    assert path in result.stderr


def test_invalid_revision_stops_before_fetch(repository):
    repo, _ = repository
    result = load(repo, "--upload-pack=bad")
    assert result.returncode == 2
    assert result.stdout == ""


@pytest.mark.parametrize("plan", ["fastmcp-issue-pr.json", "fastmcp-publish-patch.json"])
def test_every_agent_requires_the_complete_role_bundle(plan):
    subprocess.run(
        [
            "jq",
            "-e",
            "--slurpfile",
            "manifest",
            str(MANIFEST),
            """.plan as $p | all($p.nodes | to_entries[] | select(.value.kind == "AgentNode");
              .key as $role | ($role + "_instructions") as $input |
              $p.inputs[$input].required == true and
              $p.nodes[$role].inputs.instructions.expects.cardinality == "exactly_one" and
              $p.inputs[$input].schema == $p.nodes[$role].inputs.instructions.schema and
              ($p.inputs[$input].schema.properties.files.required | sort) ==
                ([$manifest[0].common[], $manifest[0].roles[$role][]] | unique) and
              any($p.edges[]; .from == {type:"plan_input",input:$input} and
                .to == {node:$role,input:"instructions"}))""",
            str(ROOT / "deploy/agentic-workflows" / plan),
        ],
        check=True,
        capture_output=True,
    )
