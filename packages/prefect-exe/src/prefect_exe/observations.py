from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field


class Stage(BaseModel):
    started_at: float
    finished_at: float | None = None
    seconds: float | None = None
    outcome: Literal["running", "completed", "failed"] = "running"


class Attempt(BaseModel):
    vm: str
    flow_run_id: str
    run_name: str | None = None
    image: str | None = None
    timeout_seconds: int | None = None
    created_at: float
    updated_at: float
    phase: Literal[
        "creating",
        "bootstrapping",
        "starting",
        "running",
        "exited",
        "retained",
        "deleted",
        "missing",
    ] = "creating"
    exit_code: int | None = None
    reason: str | None = None
    error: str | None = None
    stages: dict[str, Stage] = Field(default_factory=dict)


class AttemptStore:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = path
        with self.connect() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS attempts "
                "(vm TEXT PRIMARY KEY, updated REAL NOT NULL, closed INTEGER NOT NULL, data TEXT NOT NULL)"
            )
        path.chmod(0o600)

    def connect(self):
        return sqlite3.connect(self.path, timeout=5)

    def read(self, vm: str) -> Attempt | None:
        with self.connect() as db:
            row = db.execute("SELECT data FROM attempts WHERE vm = ?", (vm,)).fetchone()
        return Attempt.model_validate_json(row[0]) if row else None

    def save(self, attempt: Attempt) -> None:
        with self.connect() as db:
            db.execute(
                "INSERT INTO attempts VALUES (?, ?, ?, ?) ON CONFLICT(vm) DO UPDATE SET "
                "updated=excluded.updated, closed=excluded.closed, data=excluded.data",
                (
                    attempt.vm,
                    attempt.updated_at,
                    attempt.phase in {"deleted", "missing"},
                    attempt.model_dump_json(),
                ),
            )
            db.execute(
                "DELETE FROM attempts WHERE closed = 1 AND vm NOT IN "
                "(SELECT vm FROM attempts WHERE closed = 1 ORDER BY updated DESC LIMIT 1000)"
            )

    def reconcile_inventory(self, present: set[str], observed_at: float) -> None:
        with self.connect() as db:
            rows = db.execute(
                "SELECT data FROM attempts WHERE closed = 0 AND updated < ?",
                (observed_at - 600,),
            ).fetchall()
        for row in rows:
            attempt = Attempt.model_validate_json(row[0])
            if attempt.vm not in present:
                attempt.phase = "missing"
                attempt.error = "VM absent from inventory; deletion was not observed"
                attempt.updated_at = observed_at
                self.save(attempt)

    def snapshot(self) -> dict:
        with self.connect() as db:
            active = db.execute("SELECT count(*) FROM attempts WHERE closed = 0").fetchone()[0]
            rows = db.execute(
                "SELECT data FROM attempts ORDER BY closed ASC, updated DESC LIMIT 100"
            ).fetchall()
        return {"active_attempts": active, "attempts": [json.loads(row[0]) for row in rows]}
