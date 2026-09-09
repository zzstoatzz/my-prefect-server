"""Durable, revocable inference capabilities issued by the trusted worker."""

import hashlib
import os
import secrets
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path


class InferenceGrants:
    def __init__(self, database: Path):
        self.database = database
        database.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        descriptor = os.open(database, os.O_CREAT | os.O_WRONLY, 0o600)
        os.close(descriptor)
        if database.stat().st_mode & 0o077:
            raise ValueError("Inference grant database must be private to its owner")
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("""
                CREATE TABLE IF NOT EXISTS grants (
                    attempt TEXT PRIMARY KEY,
                    token_hash TEXT UNIQUE NOT NULL,
                    model TEXT NOT NULL,
                    expires REAL NOT NULL,
                    request_limit INTEGER NOT NULL,
                    requests INTEGER NOT NULL DEFAULT 0,
                    revoked INTEGER NOT NULL DEFAULT 0
                )
            """)
            columns = {row[1] for row in connection.execute("PRAGMA table_info(grants)")}
            if "token_value" not in columns:
                connection.execute("ALTER TABLE grants ADD COLUMN token_value TEXT")

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.database, timeout=10)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def acquire(
        self, attempt: str, *, model: str, lifetime: int = 1800, request_limit: int = 32
    ) -> str:
        """Recover the same bounded grant after uncertain worker submission.

        The short-lived capability is retained only in this private worker DB;
        revocation clears it. Recovery never renews expiry or replenishes usage.
        """
        if not attempt or not model or not 1 <= lifetime <= 86400 or request_limit < 1:
            raise ValueError("Invalid inference grant")
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT model, expires, request_limit, revoked, token_value FROM grants WHERE attempt = ?",
                (attempt,),
            ).fetchone()
            if row:
                if row[0] != model or row[2] != request_limit:
                    raise ValueError("Attempt already has a different inference scope")
                if row[3] or row[1] <= time.time() or not row[4]:
                    raise ValueError("Attempt inference grant cannot be recovered")
                return row[4]
            token = secrets.token_urlsafe(32)
            connection.execute(
                "INSERT INTO grants(attempt, token_hash, model, expires, request_limit, token_value) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    attempt,
                    hashlib.sha256(token.encode()).hexdigest(),
                    model,
                    time.time() + lifetime,
                    request_limit,
                    token,
                ),
            )
            return token

    def issue(
        self, attempt: str, *, model: str, lifetime: int = 1800, request_limit: int = 32
    ) -> str:
        if not attempt or not model or not 1 <= lifetime <= 86400 or request_limit < 1:
            raise ValueError("Invalid inference grant")
        token = secrets.token_urlsafe(32)
        with self.connect() as connection:
            # Duplicate attempts fail rather than silently resetting their budget.
            connection.execute(
                "INSERT INTO grants(attempt, token_hash, model, expires, request_limit) VALUES (?, ?, ?, ?, ?)",
                (
                    attempt,
                    hashlib.sha256(token.encode()).hexdigest(),
                    model,
                    time.time() + lifetime,
                    request_limit,
                ),
            )
        return token

    def consume(self, token: str, model: str) -> bool:
        if not token or len(token) > 256:
            return False
        with self.connect() as connection:
            result = connection.execute(
                """UPDATE grants SET requests = requests + 1
                   WHERE token_hash = ? AND model = ? AND expires > ?
                   AND revoked = 0 AND requests < request_limit""",
                (hashlib.sha256(token.encode()).hexdigest(), model, time.time()),
            )
            return result.rowcount == 1

    def revoke(self, attempt: str) -> None:
        with self.connect() as connection:
            connection.execute(
                "UPDATE grants SET revoked = 1, token_value = NULL WHERE attempt = ?", (attempt,)
            )

    def usage(self, attempt: str) -> dict:
        with self.connect() as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute(
                "SELECT attempt, model, expires, request_limit, requests, revoked FROM grants WHERE attempt = ?",
                (attempt,),
            ).fetchone()
            return dict(row) if row else {}
