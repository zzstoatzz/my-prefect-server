import sysconfig
import zipfile
from pathlib import Path

with zipfile.ZipFile("mps-0.1.0-py3-none-any.whl") as wheel:
    root = Path(sysconfig.get_path("purelib"))
    modules = [name for name in wheel.namelist() if name.endswith(".py")]
    for name in modules:
        if (root / name).read_bytes() != wheel.read(name):
            raise SystemExit(f"Installed module differs from release wheel: {name}")
    print(f"Verified {len(modules)} installed modules against the release wheel")
