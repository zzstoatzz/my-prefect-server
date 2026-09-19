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
    # four remote wheel artifacts, two home-pool wheel backports, arbitrary pull scripts
    assert len(unverified) == 7


@pytest.mark.parametrize(
    "name", ["watch-tangled-pulls", "autofix-revise", "merge-approved", "test-pull-patch"]
)
def test_reconciliation_regression(config, name):
    dep = deployment(config, name)
    module, function = dep["entrypoint"].rsplit(".", 1)
    dep["entrypoint"] = module.replace(".", "/") + ".py:" + function
    errors, _ = check(config)
    assert any(f"{name}: wheel-only" in error for error in errors)


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
    dep = deployment(config, "autofix-revise")
    dep["entrypoint"] = "flows.ingest.ingest"  # exists locally, absent from mps wheel
    assert any("not declared" in e for e in check(config)[0])
    dep["work_pool"]["job_variables"]["local_packages"] = ["/releases/other-1.whl"]
    assert any("requires the mps wheel" in e for e in check(config)[0])


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
