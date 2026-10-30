import importlib.util
import json
import os
import subprocess
import sys
import threading
import time
from contextlib import suppress
from pathlib import Path

import pytest
from mps import typeahead_source_budget as budget


@pytest.fixture
def source_fixture(tmp_path):
    repository = os.environ.get("TA_REPOSITORY")
    if not repository:
        pytest.skip("requires actual pinned source client and SQLite HTTP fixture")
    path = Path(repository) / "services/tantivy-input/test_sync.py"
    spec = importlib.util.spec_from_file_location("budget_source_fixture", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    fixture = module.Fixture(tmp_path)
    try:
        yield Path(repository), module.sync, fixture
    finally:
        fixture.close()


def test_byte_accounting_and_reserve_survive_process_recreation(tmp_path, source_fixture):
    _, sync, fixture = source_fixture
    ledger = tmp_path / "budget.json"
    source = budget.accounted_source(
        sync, fixture.url, fixture.source_secret, ledger, "fixture", 16000, allow_test_loopback=True
    )
    for i in range(64):
        fixture.actor(f"did:fixture:{i}", fixture.floor + 1)
    try:
        with source.budget_scope("sweep"), pytest.raises(sync.SweepBudgetExhausted):
            source.select(sync.ACTORS_SQL, [fixture.floor, fixture.floor, "", fixture.now, 500])
        assert source.sweep_bytes == 12000
    finally:
        source.close()
    recovered = budget.accounted_source(
        sync, fixture.url, fixture.source_secret, ledger, "fixture", 16000, allow_test_loopback=True
    )
    try:
        assert recovered.sweep_bytes == 12000
        assert recovered.now() == fixture.now
        assert recovered.bytes == recovered.sweep_bytes + recovered.poll_bytes <= 16000
        recorded = json.loads(ledger.read_bytes())
        assert recorded["source_bytes"] == recovered.bytes
        assert recorded["pending_reservation"] is False
    finally:
        recovered.close()


def test_sigkill_during_actual_http_keeps_pending_reservation_charged(tmp_path, source_fixture):
    repository, sync, fixture = source_fixture
    ledger = tmp_path / "budget.json"
    released = threading.Event()
    original = fixture.server.RequestHandlerClass.respond

    def delayed(self, code, value):
        released.wait(10)
        with suppress(OSError):
            original(self, code, value)

    fixture.server.RequestHandlerClass.respond = delayed
    program = """
import importlib.util,sys
from pathlib import Path
def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module
budget=load('actual_budget',sys.argv[1]);sync=load('actual_sync',sys.argv[2])
source=budget.accounted_source(sync,sys.argv[3],sys.argv[4],Path(sys.argv[5]),'fixture',16*1024*1024,allow_test_loopback=True)
source.now()
"""
    child = subprocess.Popen(
        [
            sys.executable,
            "-c",
            program,
            budget.__file__,
            str(repository / "services/tantivy-input/sync.py"),
            fixture.url,
            fixture.source_secret,
            str(ledger),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 5
        while not fixture.requests and time.monotonic() < deadline:
            time.sleep(0.01)
        assert fixture.requests
        pending = json.loads(ledger.read_bytes())
        assert pending["pending_reservation"] is True
        assert pending["source_bytes"] > 0
        child.kill()
        child.wait(timeout=5)
    finally:
        released.set()
        if child.poll() is None:
            child.kill()
            child.wait(timeout=5)
    source = budget.accounted_source(
        sync,
        fixture.url,
        fixture.source_secret,
        ledger,
        "fixture",
        16 * 1024 * 1024,
        allow_test_loopback=True,
    )
    try:
        assert source.bytes == pending["source_bytes"]
        assert source.accounting()["recovered_reservation_bytes"] == pending["reservation_bytes"]
        assert source.now() == fixture.now
        assert source.bytes > pending["source_bytes"]
    finally:
        source.close()


def test_ledger_identity_and_single_writer_are_enforced(tmp_path, source_fixture):
    _, sync, fixture = source_fixture
    ledger = tmp_path / "budget.json"
    source = budget.accounted_source(
        sync, fixture.url, fixture.source_secret, ledger, "fixture", 16000, allow_test_loopback=True
    )
    try:
        with pytest.raises(BlockingIOError):
            budget.accounted_source(
                sync,
                fixture.url,
                fixture.source_secret,
                ledger,
                "fixture",
                16000,
                allow_test_loopback=True,
            )
    finally:
        source.close()
    with pytest.raises(ValueError, match="identity changed"):
        budget.accounted_source(
            sync,
            fixture.url,
            fixture.source_secret,
            ledger,
            "different",
            16000,
            allow_test_loopback=True,
        )
    assert not fixture.requests
