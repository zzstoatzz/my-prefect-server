"""Durable conservative accounting for the offline initial reconciliation job."""

import fcntl
import json
import os
import time


def accounted_source(sync, url, token, ledger, build_id, max_bytes, *, allow_test_loopback=False):
    class AccountedTurso(sync.Turso):
        def __init__(self):
            self.recovered_reservation_bytes = 0
            super().__init__(
                url, token, max_bytes=max_bytes, allow_test_loopback=allow_test_loopback
            )
            if (
                ledger.is_symlink()
                or ledger.with_suffix(".lock").is_symlink()
                or ledger.with_suffix(".partial").is_symlink()
            ):
                raise ValueError("Offline accounting paths cannot be symlinks")
            self.lock = ledger.with_suffix(".lock").open("a+b")
            try:
                fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                self.restore()
                self.persist(self.accounting(), pending=False)
            except BaseException:
                self.lock.close()
                raise
            self.next_request = 0

        def restore(self):
            if ledger.exists():
                with ledger.open("rb") as stream:
                    raw = stream.read(4097)
                if len(raw) > 4096:
                    raise ValueError("Offline source accounting ledger too large")
                state = json.loads(raw)
                if (
                    state.get("schema_version") != 1
                    or state.get("build_id") != build_id
                    or state.get("max_source_bytes") != max_bytes
                ):
                    raise ValueError("Offline source accounting identity changed")
                for key in ("source_bytes", "poll_bytes", "sweep_bytes", "requests"):
                    if type(state.get(key)) is not int or state[key] < 0:
                        raise ValueError("Invalid offline source accounting")
                if (
                    state["source_bytes"] != state["poll_bytes"] + state["sweep_bytes"]
                    or state["source_bytes"] > max_bytes
                    or state["sweep_bytes"] > max_bytes - self.poll_reserve_bytes
                ):
                    raise ValueError("Invalid offline source accounting sum")
                recovered = state.get("recovered_reservation_bytes", 0)
                reserved = state.get("reservation_bytes", 0)
                if (
                    type(recovered) is not int
                    or recovered < 0
                    or type(reserved) is not int
                    or not 0 <= reserved <= state["source_bytes"]
                ):
                    raise ValueError("Invalid offline source reservation accounting")
                self.recovered_reservation_bytes = recovered + (
                    reserved if state.get("pending_reservation") else 0
                )
                self.bytes, self.poll_bytes, self.sweep_bytes, self.requests = (
                    state[key] for key in ("source_bytes", "poll_bytes", "sweep_bytes", "requests")
                )

        def accounting(self):
            return {
                **super().accounting(),
                "accounting_mode": "conservative-offline",
                "recovered_reservation_bytes": self.recovered_reservation_bytes,
            }

        def persist(self, accounting, *, pending, reservation_bytes=0):
            state = {
                **accounting,
                "schema_version": 1,
                "build_id": build_id,
                "requests": self.requests,
                "pending_reservation": pending,
                "reservation_bytes": reservation_bytes,
            }
            state["remaining_source_bytes"] = max(0, max_bytes - state["source_bytes"])
            state["remaining_sweep_bytes"] = max(
                0,
                min(
                    state["remaining_source_bytes"],
                    max_bytes - self.poll_reserve_bytes - state["sweep_bytes"],
                ),
            )
            temporary = ledger.with_suffix(".partial")
            with temporary.open("wb") as stream:
                stream.write(json.dumps(state, sort_keys=True).encode() + b"\n")
                stream.flush()
                os.fsync(stream.fileno())
            temporary.replace(ledger)
            descriptor = os.open(ledger.parent, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)

        def select(self, sql, args):
            wait = self.next_request - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            self.next_request = time.monotonic() + 0.5
            reservation = max(0, min(self.remaining_bytes(), (4 * 1024 * 1024 + 1) * self.retries))
            charged = self.accounting()
            charged["source_bytes"] += reservation
            charged[self.budget_kind + "_bytes"] += reservation
            self.persist(charged, pending=True, reservation_bytes=reservation)
            try:
                return super().select(sql, args)
            finally:
                self.persist(self.accounting(), pending=False)

        def close(self):
            self.lock.close()

    return AccountedTurso()


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
    args = parser.parse_args()
    if not 1 <= args.max_seconds <= 10800 or not 2 <= args.max_source_bytes <= 8 * 1024**3:
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
                max_sweep_seconds=5,
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
