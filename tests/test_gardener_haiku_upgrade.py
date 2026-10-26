import base64
import csv
import hashlib
import importlib.util
import io
import zipfile
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "upgrade_haiku", Path(__file__).resolve().parents[1] / "deploy/gardener/upgrade_haiku.py"
)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_upgrade_preserves_other_modules_and_valid_wheel_records(tmp_path):
    wheel = tmp_path / "mps.whl"
    old = b'model = "claude-haiku-4-5"\n'
    untouched = b'legacy_rate = {"claude-haiku-4-5": 1}\n'
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("mps/pi.py", old)
        archive.writestr("mps/spend.py", untouched)
        archive.writestr("mps-0.1.0.dist-info/RECORD", "")
    report = module.upgrade(wheel)
    assert report["modules"] == ["mps/pi.py"]
    with zipfile.ZipFile(wheel) as archive:
        assert archive.read("mps/pi.py") == b'model = "claude-haiku-5-5"\n'
        assert archive.read("mps/spend.py") == untouched
        rows = csv.reader(io.StringIO(archive.read("mps-0.1.0.dist-info/RECORD").decode()))
        for name, digest, size in rows:
            if name.endswith("RECORD"):
                assert (digest, size) == ("", "")
            else:
                content = archive.read(name)
                assert digest == "sha256=" + base64.urlsafe_b64encode(
                    hashlib.sha256(content).digest()
                ).decode().rstrip("=")
                assert int(size) == len(content)
    with pytest.raises(ValueError, match=r"no Haiku 4\.5"):
        module.upgrade(wheel)


def test_installed_upgrade_preserves_bytecode_records_and_rejects_drift(tmp_path):
    wheel = tmp_path / "mps.whl"
    installed = tmp_path / "installed"
    (installed / "mps").mkdir(parents=True)
    (installed / "mps-0.1.0.dist-info").mkdir()
    before = b'model = "claude-haiku-4-5"\n'
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("mps/pi.py", before)
        archive.writestr("mps-0.1.0.dist-info/RECORD", "")
    original = wheel.read_bytes()
    module_file = installed / "mps/pi.py"
    module_file.write_bytes(b"drift")
    with pytest.raises(ValueError, match="installed module differs"):
        module.upgrade(wheel, installed)
    assert wheel.read_bytes() == original
    module_file.write_bytes(before)
    record = installed / "mps-0.1.0.dist-info/RECORD"
    record.write_text("mps/pi.py,old,25\nmps/__pycache__/pi.pyc,,\n")
    module.upgrade(wheel, installed)
    rows = list(csv.reader(io.StringIO(record.read_text())))
    assert rows[1] == ["mps/__pycache__/pi.pyc", "", ""]
    assert rows[0][1] == "sha256=" + base64.urlsafe_b64encode(
        hashlib.sha256(module_file.read_bytes()).digest()
    ).decode().rstrip("=")
    assert int(rows[0][2]) == len(module_file.read_bytes())
