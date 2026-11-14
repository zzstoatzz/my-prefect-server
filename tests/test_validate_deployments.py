"""Regression cases for delivery semantics, without importing any flows."""

import copy
import importlib.util
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("validator", ROOT / "scripts/validate_deployments.py")
validator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(validator)


def check(config, **kwargs):
    return validator.validate_deployments(config, root=ROOT, **kwargs)


@pytest.fixture
def config():
    return yaml.safe_load((ROOT / "prefect.yaml").read_text())


def deployment(config, name):
    return next(d for d in config["deployments"] if d["name"] == name)


def test_repository_contracts(config):
    errors, unverified = check(config)
    assert errors == []
    assert len(unverified) == 10


@pytest.mark.parametrize(
    "name",
    [
        "watch-tangled-pulls",
        "autofix-revise",
        "merge-approved",
        "test-pull-patch",
        "investigate",
        "pi-pr",
    ],
)
def test_reconciliation_regression(config, name):
    dep = deployment(config, name)
    module, function = dep["entrypoint"].rsplit(".", 1)
    dep["entrypoint"] = module.replace(".", "/") + ".py:" + function
    errors, _ = check(config)
    assert any(
        f"{name}:" in error and ("wheel-only" in error or "image mode" in error) for error in errors
    )


def test_explicit_empty_pull_overrides_global(config):
    dep = deployment(config, "watch-tangled-pulls")
    dep["entrypoint"] = "flows/watch_tangled_pulls.py:watch_tangled_pulls"
    assert check(config)[0]
    del dep["pull"]
    assert not check(config)[0]


def test_prebuilt_image_file_is_unverified_not_invalid():
    errors, unverified = check(
        {
            "pull": [],
            "deployments": [
                {
                    "name": "image",
                    "entrypoint": "/app/flow.py:run",
                    "work_pool": {"job_variables": {"image": "example@sha256:abc"}},
                }
            ],
        }
    )
    assert not errors
    assert any("worker path or image" in warning for warning in unverified)


def test_missing_package_and_unshipped_module(config):
    dep = deployment(config, "watch-tangled-pulls")
    dep["entrypoint"] = "flows.ingest.ingest"  # exists locally, absent from mps wheel
    assert any("not declared" in e for e in check(config)[0])
    dep["work_pool"]["job_variables"]["command"] = ""
    dep["work_pool"]["job_variables"]["local_packages"] = ["/releases/other-1.whl"]
    assert any("requires the mps wheel" in e for e in check(config)[0])


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("image", "registry/app:latest"),
        ("requirements", ["prefect"]),
        ("local_packages", ["mps.whl"]),
    ],
)
def test_image_mode_rejects_mutable_or_runtime_installed_code(config, field, value):
    deployment(config, "investigate")["work_pool"]["job_variables"][field] = value
    assert any("investigate: image mode" in error for error in check(config)[0])


@pytest.mark.parametrize("pin", [None, "@main", "@" + "a" * 40])
def test_checkout_must_match_installed_package(config, pin):
    env = deployment(config, "diagnostics")["work_pool"]["job_variables"]["env"]
    env["MPS_PIN"] = pin
    assert any("diagnostics: job env.MPS_PIN" in e for e in check(config)[0])


def test_explicit_legacy_pins_remain_valid(config):
    assert not check(config, release_pin="@" + "a" * 40)[0]


@pytest.mark.parametrize("pin", ["", "@main", "abc", "@1234567"])
def test_release_requires_full_pin(config, pin):
    assert any("release:" in e for e in check(config, release_pin=pin)[0])


def test_anchors_do_not_supply_implicit_job_environment(config):
    dep = deployment(config, "diagnostics")
    del dep["work_pool"]["job_variables"]["env"]
    assert any("diagnostics:" in e for e in check(config)[0])


def test_validation_never_rewrites(config):
    before = copy.deepcopy(config)
    check(config)
    assert config == before


@pytest.mark.parametrize("bad", [None, [], {"deployments": "wrong"}, {"deployments": [None]}])
def test_malformed_config(bad):
    assert check(bad)[0]


def test_explicit_worker_path_with_wheel_is_unverified(config):
    dep = deployment(config, "watch-tangled-pulls")
    dep["entrypoint"] = "flows/watch_tangled_pulls.py:watch_tangled_pulls"
    dep["work_pool"]["job_variables"]["working_dir"] = "/opt/flows"
    errors, unverified = check(config)
    assert not errors
    assert any("worker path or image" in warning for warning in unverified)


def test_heavypad_repository_contracts():
    errors, _ = check(yaml.safe_load((ROOT / "prefect.heavypad.yaml").read_text()))
    assert errors == []


def checkout_config(command):
    return {
        "pull": [{"prefect.deployments.steps.set_working_directory": {"directory": "src"}}],
        "deployments": [
            {
                "name": "light",
                "entrypoint": "flows/light.py:light",
                "work_pool": {
                    "name": "pool",
                    "job_variables": {"command": command, "env": {"MPS_PIN": "{{ $MPS_PIN }}"}},
                },
            }
        ],
    }


def write_flows(root, **modules):
    (root / "flows").mkdir()
    for name, body in modules.items():
        (root / "flows" / f"{name}.py").write_text(body)


LIGHT = "uv run --no-project --with prefect==3.9.3 prefect flow-run execute"


def test_light_deployment_rejects_transitive_mps_import(tmp_path):
    # 2026-11-07: mcp-fleet-health imported flows.fleet_health for one helper; a
    # later fleet_health import of mps.blocks crashed every run while loading.
    write_flows(
        tmp_path,
        light="from flows.heavy import helper\n",
        heavy="import json\nfrom flows.observe import rows\n",
        observe="from mps.blocks import secret_sync\n",
    )
    errors, _ = validator.validate_deployments(checkout_config(LIGHT), root=tmp_path)
    assert errors == [
        "light: loading the flow imports mps.blocks via flows/light.py -> flows/heavy.py"
        " -> flows/observe.py, but the command does not install mps"
    ]
    for requirement in (
        "'mps @ git+https://github.com/zzstoatzz/my-prefect-server.git{{ $MPS_PIN }}"
        "#subdirectory=packages/mps'",
        "'my-prefect-server @ git+https://github.com/zzstoatzz/my-prefect-server.git"
        "{{ $MPS_PIN }}'",
    ):
        installed = LIGHT.replace(" prefect flow-run", f" --with {requirement} prefect flow-run")
        assert validator.validate_deployments(checkout_config(installed), root=tmp_path)[0] == []


def test_imports_that_do_not_run_at_load_are_allowed(tmp_path):
    write_flows(
        tmp_path,
        light=(
            "from typing import TYPE_CHECKING\n"
            "if TYPE_CHECKING:\n"
            "    from mps.blocks import secret_sync\n"
            "def light():\n"
            "    from mps.blocks import secret_sync\n"
            "    return secret_sync\n"
        ),
    )
    assert validator.validate_deployments(checkout_config(LIGHT), root=tmp_path)[0] == []
