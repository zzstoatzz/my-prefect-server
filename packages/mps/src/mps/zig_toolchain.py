"""Pick the Zig compiler a repository asks for.

The home box keeps one install per version under ~/.local/opt and a default
`zig` on PATH. Projects move between Zig versions one at a time, so a flow
that builds a project reads the version from that project's build.zig.zon
instead of trusting the default.
"""

import platform
import re
from pathlib import Path

_MIN_VERSION = re.compile(r'\.minimum_zig_version\s*=\s*"([^"]+)"')


def minimum_zig_version(build_dir: Path) -> str | None:
    """The `.minimum_zig_version` in `build_dir/build.zig.zon`, if declared."""
    try:
        text = (build_dir / "build.zig.zon").read_text()
    except OSError:
        return None
    match = _MIN_VERSION.search(text)
    return match.group(1) if match else None


def zig_for(build_dir: Path, opt: Path | None = None) -> str:
    """Path to the install matching the project's minimum version, else `zig`.

    Falling back to PATH keeps a project without a declared version, or a
    version nobody installed, building exactly as it did before.
    """
    version = minimum_zig_version(build_dir)
    if version is None:
        return "zig"
    root = opt or Path.home() / ".local" / "opt"
    candidate = root / f"zig-{platform.machine()}-linux-{version}" / "zig"
    return str(candidate) if candidate.is_file() else "zig"
