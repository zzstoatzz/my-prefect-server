"""Run the real Phi launcher with hostile filesystem/privilege checks."""

import os
import runpy
import subprocess
import sys
import tempfile
from pathlib import Path


def main():
    sandbox = runpy.run_path(sys.argv[1])
    with tempfile.TemporaryDirectory(prefix="phi-boundary-") as temporary:
        root = Path(temporary)
        paths = {name: root / name for name in ("workspace", "home", "tools")}
        for path in paths.values():
            path.mkdir()
            os.chown(path, 2000, 2000)
        canary = root / "trusted-canary"
        canary.write_text("fake-secret-for-isolation-test")
        (paths["workspace"] / "escape").symlink_to(canary)
        check = r"""
import os, socket
from pathlib import Path
assert os.getuid() == 2000
assert os.getgroups() == []
status = Path('/proc/self/status').read_text()
assert 'NoNewPrivs:\t1' in status
assert 'CapEff:\t0000000000000000' in status
for forbidden in ('/.sprite/api.sock', '/root', '/var/lib/prefect-sprites', '/workspace/escape'):
    assert not Path(forbidden).exists(), forbidden
assert 'PREFECT_API_AUTH_STRING' not in os.environ
try:
    os.setuid(0)
except PermissionError:
    pass
else:
    raise AssertionError('regained root')
assert not any(Path('/proc/net/route').read_text().splitlines()[1:])
Path('/workspace/result').write_text('writable')
Path('/home/agent/result').write_text('writable')
print('agent boundary: writable checkout/home; host files hidden; no routes; no privileges')
"""
        subprocess.run(
            sandbox["sandbox_command"](**paths, command=["/usr/bin/python3", "-c", check]),
            check=True,
            env={"PATH": "/usr/bin:/bin", "PREFECT_API_AUTH_STRING": "fake-canary"},
        )
        assert (paths["workspace"] / "result").read_text() == "writable"
        assert canary.read_text() == "fake-secret-for-isolation-test"


if __name__ == "__main__":
    main()
