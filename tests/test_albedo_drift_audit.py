import asyncio

import httpx
import pytest
from prefect.testing.utilities import prefect_test_harness

from flows import albedo_drift_audit as audit

LISTING = (
    "atlogin.service loaded active running ATLogin for login.zzstoatzz.io\n"
    "heavypad-unit-sweep.timer loaded active waiting Daily sweep of stale units\n"
    "docker.service not-found inactive dead docker.service\n"
)
SHOW = (
    "Id=atlogin.service\nFragmentPath=/home/stoat/.config/systemd/user/atlogin.service\n\n"
    "Id=heavypad-unit-sweep.timer\nFragmentPath=/home/stoat/.config/systemd/user/x.timer\n\n"
    "Id=docker.service\nFragmentPath=\n"
)


def test_snapshot_keeps_descriptions_and_paths(monkeypatch):
    monkeypatch.setattr(
        audit, "_user_systemctl", lambda *args: LISTING if args[0] == "list-units" else SHOW
    )
    units, paths = audit.snapshot_units.fn()
    assert units.splitlines()[0] == (
        "atlogin.service\tloaded\tactive\trunning\tATLogin for login.zzstoatzz.io"
    )
    assert units.splitlines()[2].startswith("docker.service\tnot-found\tinactive\tdead")
    assert paths.splitlines() == [
        "atlogin.service\t/home/stoat/.config/systemd/user/atlogin.service",
        "heavypad-unit-sweep.timer\t/home/stoat/.config/systemd/user/x.timer",
        "docker.service\t",
    ]


def _stub_audit(monkeypatch, tmp_path, status: int):
    monkeypatch.setattr(audit, "snapshot_units", lambda: ("", ""))
    monkeypatch.setattr(audit, "prepare_workspace", lambda units, paths: None)
    monkeypatch.setattr(audit, "run_audit", lambda timeout: "# report\n")
    monkeypatch.setattr(audit, "archive_report", lambda report: tmp_path / "r.md")

    async def no_artifact(**kwargs):
        return None

    async def phi(slot, material):
        request = httpx.Request("POST", f"https://phi.example/api/control/trigger/{slot}")
        raise httpx.HTTPStatusError(
            "phi", request=request, response=httpx.Response(status, request=request)
        )

    monkeypatch.setattr(audit, "acreate_markdown_artifact", no_artifact)
    monkeypatch.setattr(audit, "phi_trigger", phi)


def test_missing_phi_slot_degrades(monkeypatch, tmp_path):
    _stub_audit(monkeypatch, tmp_path, 404)
    with prefect_test_harness():
        state = asyncio.run(audit.albedo_drift_audit(return_state=True))
    assert state.name == "Degraded"


def test_other_phi_errors_fail(monkeypatch, tmp_path):
    _stub_audit(monkeypatch, tmp_path, 500)
    with prefect_test_harness(), pytest.raises(httpx.HTTPStatusError):
        asyncio.run(audit.albedo_drift_audit())
