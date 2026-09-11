import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest
from mps.inference_grants import InferenceGrants


def test_grant_is_scoped_and_survives_restart(tmp_path):
    database = tmp_path / "grants.sqlite"
    grants = InferenceGrants(database)
    token = grants.issue("flow:attempt", model="allowed", request_limit=2)
    assert not grants.consume(token, "other")
    assert not grants.consume("wrong", "allowed")
    assert grants.consume(token, "allowed")
    restored = InferenceGrants(database)
    assert restored.consume(token, "allowed")
    assert not restored.consume(token, "allowed")
    assert restored.usage("flow:attempt")["requests"] == 2
    assert token.encode() not in database.read_bytes()


def test_revoked_and_expired_grants_are_rejected(tmp_path):
    grants = InferenceGrants(tmp_path / "grants.sqlite")
    token = grants.issue("revoked", model="allowed")
    grants.revoke("revoked")
    assert not grants.consume(token, "allowed")
    token = grants.issue("expired", model="allowed")
    with grants.connect() as connection:
        connection.execute("UPDATE grants SET expires = 0 WHERE attempt = 'expired'")
    assert not grants.consume(token, "allowed")


def test_concurrent_requests_cannot_exceed_limit(tmp_path):
    grants = InferenceGrants(tmp_path / "grants.sqlite")
    token = grants.issue("concurrent", model="allowed", request_limit=3)
    with ThreadPoolExecutor(max_workers=8) as executor:
        accepted = list(executor.map(lambda _: grants.consume(token, "allowed"), range(20)))
    assert sum(accepted) == 3


def test_reissuing_attempt_cannot_reset_budget(tmp_path):
    grants = InferenceGrants(tmp_path / "grants.sqlite")
    token = grants.issue("same", model="allowed", request_limit=1)
    assert grants.consume(token, "allowed")
    with pytest.raises(sqlite3.IntegrityError):
        grants.issue("same", model="allowed", request_limit=100)
    assert not grants.consume(token, "allowed")


def test_worker_recovery_preserves_token_expiry_and_consumed_budget(tmp_path):
    database = tmp_path / "grants.sqlite"
    grants = InferenceGrants(database)
    token = grants.acquire("attempt", model="allowed", lifetime=60, request_limit=1)
    assert grants.consume(token, "allowed")
    expiry = grants.usage("attempt")["expires"]
    restored = InferenceGrants(database)
    assert restored.acquire("attempt", model="allowed", lifetime=600, request_limit=1) == token
    assert restored.usage("attempt")["expires"] == expiry
    assert not restored.consume(token, "allowed")
    assert database.stat().st_mode & 0o777 == 0o600


def test_concurrent_worker_acquisition_creates_only_one_grant(tmp_path):
    grants = InferenceGrants(tmp_path / "grants.sqlite")
    with ThreadPoolExecutor(max_workers=8) as executor:
        tokens = list(executor.map(lambda _: grants.acquire("attempt", model="allowed"), range(16)))
    assert len(set(tokens)) == 1


def test_revocation_clears_recoverable_capability(tmp_path):
    grants = InferenceGrants(tmp_path / "grants.sqlite")
    token = grants.acquire("attempt", model="allowed")
    grants.revoke("attempt")
    assert not grants.consume(token, "allowed")
    with pytest.raises(ValueError, match="cannot be recovered"):
        grants.acquire("attempt", model="allowed")
    with grants.connect() as connection:
        assert connection.execute("SELECT token_value FROM grants").fetchone() == (None,)


def test_recovery_cannot_change_scope(tmp_path):
    grants = InferenceGrants(tmp_path / "grants.sqlite")
    grants.acquire("attempt", model="allowed")
    with pytest.raises(ValueError, match="different inference scope"):
        grants.acquire("attempt", model="other")
