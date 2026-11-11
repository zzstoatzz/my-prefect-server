import argparse
import os
import signal
import subprocess
import sys
import tempfile
import time
from contextlib import suppress


def run(command: list[str], parent: int) -> int:
    if os.getppid() != parent:
        return 125

    interrupting = watching = False

    def interrupted(signum, frame):
        # The child can be running before Popen returns. Exiting from inside
        # the launch would leave it alive with nothing to stop it.
        nonlocal interrupting
        interrupting = True
        if watching:
            raise SystemExit(128 + signum)

    signal.signal(signal.SIGTERM, interrupted)
    temporary = tempfile.TemporaryDirectory(prefix="typeahead-command-")
    child = subprocess.Popen(
        command, start_new_session=True, env={**os.environ, "TMPDIR": temporary.name}
    )
    try:
        watching = True
        if interrupting:
            raise SystemExit(128 + signal.SIGTERM)
        while child.poll() is None:
            if os.getppid() != parent:
                return 125
            time.sleep(0.1)
        return child.returncode
    finally:
        with suppress(ProcessLookupError):
            os.killpg(child.pid, signal.SIGTERM)
        with suppress(subprocess.TimeoutExpired):
            child.wait(timeout=5)
        with suppress(ProcessLookupError):
            os.killpg(child.pid, signal.SIGKILL)
        child.wait()
        temporary.cleanup()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--parent", type=int, required=True)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("a command is required")
    sys.exit(run(command, args.parent))


if __name__ == "__main__":
    main()
