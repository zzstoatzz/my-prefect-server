import subprocess
import sys
from pathlib import Path


def test_dbt_cli_initializes_with_the_current_python():
    result = subprocess.run(
        [str(Path(sys.executable).with_name("dbt")), "--version"],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert "Core:" in result.stdout
