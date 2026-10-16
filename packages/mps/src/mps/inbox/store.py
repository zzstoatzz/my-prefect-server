from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

Provider = Literal["gmail", "proton"]
Action = Literal["read", "keep", "defer", "ignore", "reply", "reopen"]


class Message(BaseModel):
    provider: Provider
    account: str
    remote_id: str
    message_id: str = ""
    sender: str
    subject: str
    body: str
    received_at: str
    unread: bool
    category: str = ""
    url: str = ""
    content_complete: bool = True

    @property
    def key(self) -> str:
        return hashlib.sha256(
            f"{self.provider}\0{self.account}\0{self.remote_id}".encode()
        ).hexdigest()


class Decision(BaseModel):
    ids: list[str] = Field(min_length=1, max_length=500)
    action: Action
    request_id: str = Field(min_length=16, max_length=100)
    until: float | None = None
    note: str = Field(default="", max_length=4000)


def priority(message: Message) -> tuple[int, str, str]:
    text = (message.sender + " " + message.subject + " " + message.body[:1200]).lower()
    if any(
        t in text
        for t in (
            "payment failed",
            "past due",
            "action required",
            "security alert",
            "suspicious",
            "flight cancelled",
            "deadline",
            "overdue",
            "cost spiked",
            "sign-in alert",
            "new login",
        )
    ):
        return 0, "Check first", "Possible deadline, account alert, or payment issue"
    if message.category in ("promotional", "CATEGORY_PROMOTIONS", "CATEGORY_SOCIAL") or any(
        t in message.subject.lower()
        for t in ("newsletter", "daily digest", "weekly digest", "the post most:", "the 7:")
    ):
        return 3, "Bulk review", "Mailing-list or promotional message"
    if any(
        t in text
        for t in ("receipt", "your order", "invoice", "notification", "noreply", "no-reply")
    ):
        return 2, "Updates", "Receipt or automated update"
    return 1, "Needs a look", "Potential direct correspondence"


class Store:
    def __init__(self, path: str):
        self.path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS messages (
                    id TEXT PRIMARY KEY, provider TEXT NOT NULL, account TEXT NOT NULL,
                    payload TEXT NOT NULL, rank INTEGER NOT NULL, bucket TEXT NOT NULL,
                    reason TEXT NOT NULL, group_key TEXT NOT NULL, present INTEGER NOT NULL DEFAULT 1,
                    decision TEXT, deferred_until REAL, note TEXT NOT NULL DEFAULT '',
                    updated REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS sources (
                    provider TEXT PRIMARY KEY, account TEXT, synced REAL, error TEXT, total INTEGER
                );
                CREATE TABLE IF NOT EXISTS operations (
                    request_id TEXT PRIMARY KEY, payload TEXT NOT NULL, result TEXT, created REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS history (
                    seq INTEGER PRIMARY KEY, message_id TEXT NOT NULL, action TEXT NOT NULL,
                    note TEXT NOT NULL, at REAL NOT NULL, request_id TEXT NOT NULL
                );
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def ingest(self, provider: Provider, account: str, messages: list[Message], *, complete: bool):
        now = time.time()
        with self.connect() as db:
            if complete:
                db.execute(
                    "UPDATE messages SET present=0 WHERE provider=? AND account=?",
                    (provider, account),
                )
            for message in messages:
                rank, bucket, reason = priority(message)
                group = message.message_id or message.key
                if rank == 3:
                    group = "bulk:" + message.sender.lower()
                db.execute(
                    """INSERT INTO messages(id,provider,account,payload,rank,bucket,reason,group_key,updated)
                    VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
                    payload=excluded.payload, rank=excluded.rank, bucket=excluded.bucket,
                    reason=excluded.reason, group_key=excluded.group_key, present=1, updated=excluded.updated""",
                    (
                        message.key,
                        provider,
                        account,
                        message.model_dump_json(),
                        rank,
                        bucket,
                        reason,
                        group,
                        now,
                    ),
                )
            db.execute(
                """INSERT INTO sources VALUES(?,?,?,?,?) ON CONFLICT(provider) DO UPDATE SET
                account=excluded.account,synced=excluded.synced,error=NULL,total=excluded.total""",
                (provider, account, now, None, len(messages) if complete else None),
            )

    def source_error(self, provider: str, error: str):
        with self.connect() as db:
            db.execute(
                """INSERT INTO sources(provider,error) VALUES(?,?) ON CONFLICT(provider)
                DO UPDATE SET error=excluded.error""",
                (provider, error),
            )

    def get(self, key: str) -> Message:
        with self.connect() as db:
            row = db.execute("SELECT payload FROM messages WHERE id=?", (key,)).fetchone()
        if not row:
            raise ValueError("Message no longer exists")
        return Message.model_validate_json(row["payload"])

    def queue(self, view: str = "unreviewed", limit: int = 100, offset: int = 0) -> dict:
        if (
            view not in ("unreviewed", "actionable", "deferred", "reviewed")
            or not 1 <= limit <= 500
            or offset < 0
        ):
            raise ValueError("Invalid queue selection")
        now = time.time()
        condition = {
            "unreviewed": "present=1 AND (decision IS NULL OR decision='reopen' OR (decision='defer' AND deferred_until<=?))",
            "actionable": "decision IN ('keep','reply') AND ? IS NOT NULL",
            "deferred": "decision='defer' AND deferred_until>?",
            "reviewed": "decision IS NOT NULL AND decision!='reopen' AND ? IS NOT NULL",
        }[view]
        with self.connect() as db:
            rows = db.execute(
                f"SELECT * FROM messages WHERE {condition} ORDER BY rank, json_extract(payload,'$.received_at') DESC, id LIMIT ? OFFSET ?",
                (now, limit, offset),
            ).fetchall()
            total = db.execute(
                f"SELECT count(*) FROM messages WHERE {condition}", (now,)
            ).fetchone()[0]
            pending = db.execute(
                "SELECT count(*) FROM messages WHERE present=1 AND (decision IS NULL OR decision='reopen' OR (decision='defer' AND deferred_until<=?))",
                (now,),
            ).fetchone()[0]
            counts = dict(
                db.execute(
                    "SELECT coalesce(decision,'unreviewed'),count(*) FROM messages WHERE present=1 GROUP BY decision"
                ).fetchall()
            )
            sources = [dict(r) for r in db.execute("SELECT * FROM sources")]
        items = []
        for row in rows:
            item = json.loads(row["payload"])
            item.update(
                {
                    k: row[k]
                    for k in (
                        "id",
                        "rank",
                        "bucket",
                        "reason",
                        "group_key",
                        "decision",
                        "deferred_until",
                        "note",
                    )
                }
            )
            items.append(item)
        return {
            "items": items,
            "total": total,
            "offset": offset,
            "remaining": pending,
            "counts": counts,
            "sources": sources,
        }

    def begin(self, decision: Decision) -> dict | None:
        payload = decision.model_dump_json()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM operations WHERE request_id=?", (decision.request_id,)
            ).fetchone()
            if row:
                if row["payload"] != payload:
                    raise ValueError("This request ID already belongs to a different action")
                if row["result"] is None:
                    raise ValueError(
                        "This action is still running or its result is uncertain. Refresh before retrying."
                    )
                return json.loads(row["result"])
            db.execute(
                "INSERT INTO operations VALUES(?,?,NULL,?)",
                (decision.request_id, payload, time.time()),
            )
        return None

    def record(self, key: str, decision: Decision):
        with self.connect() as db:
            row = db.execute("SELECT payload FROM messages WHERE id=?", (key,)).fetchone()
            payload = json.loads(row["payload"])
            if decision.action in ("read", "keep"):
                payload["unread"] = decision.action == "keep"
            db.execute(
                "UPDATE messages SET decision=?,deferred_until=?,note=?,payload=?,present=CASE WHEN ?='ignore' THEN 0 ELSE present END WHERE id=?",
                (
                    decision.action,
                    decision.until,
                    decision.note,
                    json.dumps(payload),
                    decision.action,
                    key,
                ),
            )
            db.execute(
                "INSERT INTO history(message_id,action,note,at,request_id) VALUES(?,?,?,?,?)",
                (key, decision.action, decision.note, time.time(), decision.request_id),
            )

    def finish(self, request_id: str, result: dict):
        with self.connect() as db:
            db.execute(
                "UPDATE operations SET result=? WHERE request_id=?",
                (json.dumps(result), request_id),
            )
