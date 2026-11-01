"""DuckDB write helpers shared across flows."""

import datetime
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path

import duckdb

from mps.email import EmailClassification, EmailItem
from mps.email_triage import Candidate
from mps.fastmcp_items import COLUMNS as FASTMCP_COLUMNS, CREATE_TABLE as FASTMCP_TABLE, FastmcpItem
from mps.likes import LikedPost
from mps.lock import analytics_write_slot
from mps.phi import PhiInteraction, PhiObservation


def _count(con: duckdb.DuckDBPyConnection, table: str) -> int:
    row = con.execute(f"SELECT count(*) FROM {table}").fetchone()
    if row is None:
        raise RuntimeError(f"count(*) on {table} returned no row")
    return int(row[0])


def _upsert_changed(
    con: duckdb.DuckDBPyConnection,
    table: str,
    key: Sequence[str],
    columns: Sequence[str],
    rows: Sequence[tuple],
) -> None:
    """Insert new rows and replace changed ones, leaving unchanged rows alone.

    `INSERT OR REPLACE` of every fetched row leaves a dead row version behind
    each run; hourly refetches grew analytics.duckdb to 5 GiB of deleted rows.
    `fetched_at` is not compared, so refetching an unchanged row writes nothing.
    """
    if not rows:
        return
    quoted = [f'"{c}"' for c in columns]
    con.execute(
        f"CREATE OR REPLACE TEMP TABLE _incoming AS SELECT {', '.join(quoted)} FROM {table} LIMIT 0"
    )
    con.executemany(f"INSERT INTO _incoming VALUES ({', '.join('?' for _ in columns)})", rows)
    match = " AND ".join(f't."{k}" = i."{k}"' for k in key)
    changed = " OR ".join(
        f't."{c}" IS DISTINCT FROM i."{c}"' for c in columns if c not in key and c != "fetched_at"
    )
    con.execute(
        f"INSERT OR REPLACE INTO {table} ({', '.join(quoted)}) "
        f"SELECT {', '.join(f'i.{q}' for q in quoted)} FROM _incoming i "
        f'LEFT JOIN {table} t ON {match} WHERE t."{key[0]}" IS NULL OR {changed}'
    )
    con.execute("DROP TABLE _incoming")


@contextmanager
def _write_conn(db_path: str) -> Iterator[duckdb.DuckDBPyConnection]:
    """RW connection that holds the single analytics writer slot for its lifetime."""
    with analytics_write_slot():
        con = duckdb.connect(db_path)
        try:
            yield con
        finally:
            con.close()


def write_liked_posts(items: list[LikedPost], db_path: str) -> int:
    """Upsert resolved liked posts into raw_liked_posts. Returns total row count."""
    with _write_conn(db_path) as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS raw_liked_posts (
                subject_uri VARCHAR PRIMARY KEY,
                author_handle VARCHAR,
                author_did VARCHAR,
                text VARCHAR,
                created_at VARCHAR,
                liked_at VARCHAR,
                embed_type VARCHAR,
                embed_text VARCHAR,
                fetched_at TIMESTAMP DEFAULT now()
            )
        """)
        rows = [
            (
                item.subject_uri,
                item.author_handle,
                item.author_did,
                item.text,
                item.created_at,
                item.liked_at,
                item.embed_type,
                item.embed_text,
                datetime.datetime.now(datetime.UTC),
            )
            for item in items
        ]
        _upsert_changed(
            con,
            "raw_liked_posts",
            ("subject_uri",),
            (
                "subject_uri",
                "author_handle",
                "author_did",
                "text",
                "created_at",
                "liked_at",
                "embed_type",
                "embed_text",
                "fetched_at",
            ),
            rows,
        )
        return _count(con, "raw_liked_posts")


def write_github_issues(items: list, db_path: str) -> int:
    """Upsert IssueOrPR items into raw_github_issues. Returns total row count."""
    with _write_conn(db_path) as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS raw_github_issues (
                repo VARCHAR, number INTEGER, type VARCHAR,
                title VARCHAR, state VARCHAR, body VARCHAR, url VARCHAR,
                labels VARCHAR[], created_at VARCHAR, updated_at VARCHAR,
                "user" VARCHAR, comments INTEGER, reactions_total INTEGER,
                fetched_at TIMESTAMP DEFAULT now(),
                PRIMARY KEY (repo, number)
            )
        """)
        rows = [
            (
                item.repo,
                item.number,
                item.type,
                item.title,
                item.state,
                item.body,
                item.url,
                item.labels,
                item.created_at,
                item.updated_at,
                item.user,
                item.comments,
                item.reactions_total,
                datetime.datetime.now(datetime.UTC),
            )
            for item in items
        ]
        _upsert_changed(
            con,
            "raw_github_issues",
            ("repo", "number"),
            (
                "repo",
                "number",
                "type",
                "title",
                "state",
                "body",
                "url",
                "labels",
                "created_at",
                "updated_at",
                "user",
                "comments",
                "reactions_total",
                "fetched_at",
            ),
            rows,
        )
        return _count(con, "raw_github_issues")


def write_tangled_items(items: list, db_path: str) -> int:
    """Upsert TangledItem objects into raw_tangled_items. Returns total row count."""
    with _write_conn(db_path) as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS raw_tangled_items (
                repo VARCHAR, kind VARCHAR, title VARCHAR,
                body VARCHAR, url VARCHAR, at_uri VARCHAR,
                author_did VARCHAR, author_handle VARCHAR,
                created_at VARCHAR, parent_uri VARCHAR,
                fetched_at TIMESTAMP DEFAULT now(),
                PRIMARY KEY (at_uri)
            )
        """)
        rows = [
            (
                item.repo,
                item.kind,
                item.title,
                item.body,
                item.url,
                item.at_uri,
                item.author_did,
                item.author_handle,
                item.created_at,
                item.parent_uri,
                datetime.datetime.now(datetime.UTC),
            )
            for item in items
        ]
        _upsert_changed(
            con,
            "raw_tangled_items",
            ("at_uri",),
            (
                "repo",
                "kind",
                "title",
                "body",
                "url",
                "at_uri",
                "author_did",
                "author_handle",
                "created_at",
                "parent_uri",
                "fetched_at",
            ),
            rows,
        )
        return _count(con, "raw_tangled_items")


def write_phi_observations(items: list[PhiObservation], db_path: str) -> int:
    """Upsert phi observations into raw_phi_observations. Returns total row count."""
    with _write_conn(db_path) as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS raw_phi_observations (
                handle VARCHAR, observation_id VARCHAR,
                content VARCHAR, tags VARCHAR[],
                created_at VARCHAR,
                fetched_at TIMESTAMP DEFAULT now(),
                PRIMARY KEY (observation_id)
            )
        """)
        rows = [
            (
                item.handle,
                item.observation_id,
                item.content,
                item.tags,
                item.created_at,
                datetime.datetime.now(datetime.UTC),
            )
            for item in items
        ]
        _upsert_changed(
            con,
            "raw_phi_observations",
            ("observation_id",),
            ("handle", "observation_id", "content", "tags", "created_at", "fetched_at"),
            rows,
        )
        return _count(con, "raw_phi_observations")


def write_phi_interactions(items: list[PhiInteraction], db_path: str) -> int:
    """Upsert phi interactions into raw_phi_interactions. Returns total row count."""
    with _write_conn(db_path) as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS raw_phi_interactions (
                handle VARCHAR, interaction_id VARCHAR,
                content VARCHAR, created_at VARCHAR,
                fetched_at TIMESTAMP DEFAULT now(),
                PRIMARY KEY (interaction_id)
            )
        """)
        rows = [
            (
                item.handle,
                item.interaction_id,
                item.content,
                item.created_at,
                datetime.datetime.now(datetime.UTC),
            )
            for item in items
        ]
        _upsert_changed(
            con,
            "raw_phi_interactions",
            ("interaction_id",),
            ("handle", "interaction_id", "content", "created_at", "fetched_at"),
            rows,
        )
        return _count(con, "raw_phi_interactions")


def write_emails(items: list[EmailItem], db_path: str) -> int:
    """Upsert EmailItem objects into raw_emails. Returns total row count."""
    with _write_conn(db_path) as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS raw_emails (
                message_id VARCHAR PRIMARY KEY,
                subject VARCHAR, sender_name VARCHAR, sender_address VARCHAR,
                snippet VARCHAR, received_at VARCHAR,
                unread BOOLEAN, mailbox VARCHAR,
                fetched_at TIMESTAMP DEFAULT now()
            )
        """)
        rows = [
            (
                item.message_id,
                item.subject,
                item.sender_name,
                item.sender_address,
                item.snippet,
                item.received_at,
                item.unread,
                item.mailbox,
                datetime.datetime.now(datetime.UTC),
            )
            for item in items
        ]
        _upsert_changed(
            con,
            "raw_emails",
            ("message_id",),
            (
                "message_id",
                "subject",
                "sender_name",
                "sender_address",
                "snippet",
                "received_at",
                "unread",
                "mailbox",
                "fetched_at",
            ),
            rows,
        )
        return _count(con, "raw_emails")


def unclassified_emails(db_path: str, limit: int = 100) -> list[tuple[str, str, str, str]]:
    """Emails with no classification yet: (message_id, sender, subject, snippet)."""
    con = duckdb.connect(db_path, read_only=True)
    try:
        con.execute("SELECT 1 FROM raw_email_classifications LIMIT 1")
    except duckdb.CatalogException:
        con.close()
        con = duckdb.connect(db_path, read_only=True)
        rows = con.execute(
            "SELECT message_id, sender_address, subject, snippet FROM raw_emails LIMIT ?",
            [limit],
        ).fetchall()
        con.close()
        return rows
    rows = con.execute(
        """
        SELECT e.message_id, e.sender_address, e.subject, e.snippet
        FROM raw_emails e
        LEFT JOIN raw_email_classifications c ON e.message_id = c.message_id
        WHERE c.message_id IS NULL
        LIMIT ?
        """,
        [limit],
    ).fetchall()
    con.close()
    return rows


def write_email_classifications(items: list[EmailClassification], db_path: str) -> int:
    """Upsert LLM email classifications. Returns total row count."""
    with _write_conn(db_path) as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS raw_email_classifications (
                message_id VARCHAR PRIMARY KEY,
                category VARCHAR,
                classified_at TIMESTAMP DEFAULT now()
            )
        """)
        con.execute(
            "ALTER TABLE raw_email_classifications ADD COLUMN IF NOT EXISTS confidence DOUBLE"
        )
        now = datetime.datetime.now(datetime.UTC)
        rows = [(item.message_id, item.category, now, item.confidence) for item in items]
        if rows:
            con.executemany(
                """
                INSERT OR REPLACE INTO raw_email_classifications
                    (message_id, category, classified_at, confidence)
                VALUES (?, ?, ?, ?)
                """,
                rows,
            )
        return _count(con, "raw_email_classifications")


def recent_classified_emails(db_path: str, hours: int = 36) -> list[Candidate]:
    """Emails received in the window, with Jev's category and confidence.

    Reads a snapshot copy so it never contends with the single writer."""
    con = duckdb.connect(db_path, read_only=True)
    try:
        con.execute("SELECT 1 FROM raw_email_classifications LIMIT 1")
    except duckdb.CatalogException:
        con.close()
        return []
    rows = con.execute(
        """
        SELECT e.message_id, coalesce(e.sender_name, ''), e.sender_address, coalesce(e.subject, ''),
               coalesce(e.snippet, ''), e.received_at, c.category, c.confidence
        FROM raw_emails e
        JOIN raw_email_classifications c USING (message_id)
        WHERE try_cast(e.received_at AS TIMESTAMPTZ) >= now() - to_hours(?)
        ORDER BY e.received_at DESC
        """,
        [hours],
    ).fetchall()
    con.close()
    return [
        Candidate(
            message_id=r[0],
            sender_name=r[1],
            sender_address=r[2],
            subject=r[3],
            snippet=r[4],
            received_at=str(r[5]),
            category=r[6],
            confidence=r[7],
        )
        for r in rows
    ]


def write_email_triage(
    decisions: Sequence[tuple[str, str, str]], run_id: str, instructions: str, db_path: str
) -> int:
    """Record the owner's decisions: (message_id, action, note) per shortlisted email."""
    with _write_conn(db_path) as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS raw_email_triage (
                message_id VARCHAR,
                action VARCHAR,
                note VARCHAR,
                run_id VARCHAR,
                instructions VARCHAR,
                decided_at TIMESTAMP DEFAULT now(),
                PRIMARY KEY (message_id, run_id)
            )
        """)
        now = datetime.datetime.now(datetime.UTC)
        con.executemany(
            "INSERT OR REPLACE INTO raw_email_triage VALUES (?, ?, ?, ?, ?, ?)",
            [(m, a, n, run_id, instructions, now) for m, a, n in decisions],
        )
        return _count(con, "raw_email_triage")


def fastmcp_watermark(db_path: str) -> datetime.datetime | None:
    """Latest `updated_at` stored for fastmcp, or None before the first sync."""
    if not Path(db_path).exists():
        return None
    con = duckdb.connect(db_path, read_only=True)
    try:
        row = con.execute("SELECT max(updated_at) FROM raw_fastmcp_items").fetchone()
    except duckdb.CatalogException:
        return None
    finally:
        con.close()
    return row[0] if row else None


def write_fastmcp_items(items: Sequence[FastmcpItem], db_path: str) -> int:
    """Upsert fastmcp issues and PRs into raw_fastmcp_items. Returns total row count."""
    with _write_conn(db_path) as con:
        con.execute(FASTMCP_TABLE)
        now = datetime.datetime.now(datetime.UTC)
        _upsert_changed(
            con,
            "raw_fastmcp_items",
            ("number",),
            FASTMCP_COLUMNS,
            [item.row(now) for item in items],
        )
        return _count(con, "raw_fastmcp_items")
