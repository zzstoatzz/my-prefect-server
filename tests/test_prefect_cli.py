import importlib.util
import json
import subprocess
from pathlib import Path
from unittest.mock import Mock

import pytest


@pytest.mark.parametrize(
    ("arguments", "command"),
    [
        (["deployment", "ls"], ["prefect", "deployment", "ls"]),
        (
            ["--script", "scripts/coding_jobs_audit.py", "/tmp/report with spaces", "--limit", "3"],
            ["python", "scripts/coding_jobs_audit.py", "/tmp/report with spaces", "--limit", "3"],
        ),
        (
            ["--script", "-m", "scripts.check_autofix_policy", "--rounds", "1"],
            ["python", "-m", "scripts.check_autofix_policy", "--rounds", "1"],
        ),
    ],
)
def test_operator_command_uses_selected_target_and_preserves_arguments(
    monkeypatch, arguments, command
):
    scripts = Path(__file__).resolve().parents[1] / "scripts"
    monkeypatch.syspath_prepend(str(scripts))
    spec = importlib.util.spec_from_file_location("prefect_cli", scripts / "prefect_cli.py")
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    monkeypatch.setattr(
        cli,
        "target_config",
        lambda: {
            "api_url": "https://production.invalid/api",
            "auth_sops_section": "production",
            "auth_sops_field": "api_auth_string",
        },
    )
    monkeypatch.setenv("PREFECT_API_URL", "https://unrelated.invalid/api")
    monkeypatch.setenv("PREFECT_API_AUTH_STRING", "wrong:credential")
    monkeypatch.setattr(cli.sys, "argv", ["prefect_cli.py", *arguments])
    run = Mock(
        side_effect=[
            subprocess.CompletedProcess([], 0, json.dumps({"api_auth_string": "test:secret"})),
            subprocess.CompletedProcess([], 7),
        ]
    )
    monkeypatch.setattr(cli.subprocess, "run", run)

    assert cli.main() == 7
    decrypt, execute = run.call_args_list
    assert decrypt.args[0][:6] == [
        "sops",
        "decrypt",
        "--output-type",
        "json",
        "--extract",
        '["production"]',
    ]
    assert decrypt.kwargs["capture_output"] is True
    assert execute.args[0] == ["uv", "run", "--with", "prefect==3.9.3", *command]
    assert execute.kwargs["env"]["PREFECT_API_URL"] == "https://production.invalid/api"
    assert execute.kwargs["env"]["PREFECT_API_AUTH_STRING"] == "test:secret"
