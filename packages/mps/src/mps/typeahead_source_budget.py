"""Offline source worker delegating accounting to the pinned Typeahead client."""

import importlib.util
import os
import sys
import time
from pathlib import Path


def accounted_source(
    sync,
    url,
    token,
    ledger,
    build_id,
    max_bytes,
    *,
    allow_test_loopback=False,
    min_request_interval=0.5,
):
    path = Path(sync.__file__).resolve().with_name("budget.py")
    spec = importlib.util.spec_from_file_location("_mps_pinned_source_budget", path)
    if spec is None or spec.loader is None:
        raise ValueError("Cannot load pinned Typeahead source accounting")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.accounted_source(
        sync,
        url,
        token,
        ledger,
        build_id,
        max_bytes,
        allow_test_loopback=allow_test_loopback,
        min_request_interval=min_request_interval,
    )


def main():
    import argparse
    import importlib.util
    import signal
    import sys
    from pathlib import Path

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--admin-url", required=True)
    parser.add_argument("--status-url", required=True)
    parser.add_argument("--max-source-bytes", type=int, required=True)
    parser.add_argument("--max-seconds", type=int, required=True)
    parser.add_argument("--minimum-select-interval", type=float, default=0.5)
    args = parser.parse_args()
    if (
        not 1 <= args.max_seconds <= 36000
        or not 2 <= args.max_source_bytes <= 8 * 1024**3
        or not 0.5 <= args.minimum_select_interval <= 5
    ):
        raise ValueError("Invalid bounded offline source settings")
    modules = []
    for name, filename in [
        ("offline_sync", "sync.py"),
        ("offline_current_state", "current_state.py"),
    ]:
        spec = importlib.util.spec_from_file_location(
            name, args.repository / "services/tantivy-input" / filename
        )
        if spec is None or spec.loader is None:
            raise ValueError("Cannot load pinned source client")
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        modules.append(module)
    sync, current = modules
    for key in ("TURSO_URL", "TURSO_AUTH_TOKEN", "ADMIN_SECRET"):
        if not os.environ.get(key):
            raise sync.SafeError("missing required cloud-only credential variable " + key)
    admin = sync.Admin(args.admin_url, os.environ["ADMIN_SECRET"], args.status_url)
    status = admin.status()
    source = accounted_source(
        sync,
        os.environ["TURSO_URL"],
        os.environ["TURSO_AUTH_TOKEN"],
        args.workspace / "source-budget.json",
        status["build_id"],
        args.max_source_bytes,
        min_request_interval=args.minimum_select_interval,
    )
    deadline = time.monotonic() + args.max_seconds

    def interrupted(*_):
        raise SystemExit("Offline source worker stopped; accounting retained")

    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    try:
        while time.monotonic() < deadline:
            result = current.ObservedSynchronizer(
                source,
                admin,
                sync,
                page_size=500,
                max_pages=256,
                max_seconds=min(300, deadline - time.monotonic()),
                anti_entropy_pages=32,
                policy_mode="snapshot",
                max_sweep_seconds=30,
                status_file=str(args.workspace / "replay-status.json"),
            ).run()
            if result.get("sweep_pause") == "budget":
                raise sync.SweepBudgetExhausted(
                    "Offline sweep allocation exhausted; accounting retained"
                )
            time.sleep(1)
        raise TimeoutError(
            "Offline source qualification deadline; durable cursor and accounting retained"
        )
    finally:
        source.close()


if __name__ == "__main__":
    main()
