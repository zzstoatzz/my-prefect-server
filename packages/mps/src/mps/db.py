"""DuckDB write helpers shared across flows."""

import datetime
from collections.abc import Iterator
from contextlib import contextmanager

import duckdb

from mps.email import EmailClassification, EmailItem
from mps.likes import LikedPost, LikeRecord
from mps.lock import analytics_write_slot
from mps.phi import PhiInteraction, PhiObservation


@contextmanager
def _write_conn(db_path: str) -> Iterator[duckdb.DuckDBPyConnection]:
    """RW connection that holds the single analytics writer slot for its lifetime."""
    with analytics_write_slot():
        con = duckdb.connect(db_path)
        try:
            yield con
        finally:
            con.close()


def write_likes(items: list[LikeRecord], db_path: str) -> int:
    """Upsert like records into raw_likes. Returns total row count."""
    with _write_conn(db_path) as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS raw_likes (
                at_uri VARCHAR PRIMARY KEY,
                subject_uri VARCHAR,
                created_at VARCHAR,
                fetched_at TIMESTAMP DEFAULT now()
            )
        """)
        rows = [
            (
                item.at_uri,
                item.subject_uri,
                item.created_at,
                datetime.datetime.now(datetime.UTC),
            )
            for item in items
        ]
        if rows:
            con.executemany(
                "INSERT OR REPLACE INTO raw_likes VALUES (?, ?, ?, ?)",
                rows,
            )
        return con.execute("SELECT count(*) FROM raw_likes").fetchone()[0]


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
        if rows:
            con.executemany(
                "INSERT OR REPLACE INTO raw_liked_posts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                rows,
            )
        return con.execute("SELECT count(*) FROM raw_liked_posts").fetchone()[0]


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
        con.executemany(
            "INSERT OR REPLACE INTO raw_github_issues VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        return con.execute("SELECT count(*) FROM raw_github_issues").fetchone()[0]


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
        if rows:
            con.executemany(
                "INSERT OR REPLACE INTO raw_tangled_items VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                rows,
            )
        return con.execute("SELECT count(*) FROM raw_tangled_items").fetchone()[0]


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
        if rows:
            con.executemany(
                "INSERT OR REPLACE INTO raw_phi_observations VALUES (?, ?, ?, ?, ?, ?)",
                rows,
            )
        return con.execute("SELECT count(*) FROM raw_phi_observations").fetchone()[0]


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
        if rows:
            con.executemany(
                "INSERT OR REPLACE INTO raw_phi_interactions VALUES (?, ?, ?, ?, ?)",
                rows,
            )
        return con.execute("SELECT count(*) FROM raw_phi_interactions").fetchone()[0]


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
        if rows:
            con.executemany(
                """INSERT OR REPLACE INTO raw_emails
                   (message_id, subject, sender_name, sender_address, snippet,
                    received_at, unread, mailbox, fetched_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                rows,
            )
        return con.execute("SELECT count(*) FROM raw_emails").fetchone()[0]


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
        rows = [
            (item.message_id, item.category, datetime.datetime.now(datetime.UTC)) for item in items
        ]
        if rows:
            con.executemany(
                "INSERT OR REPLACE INTO raw_email_classifications VALUES (?, ?, ?)",
                rows,
            )
        return con.execute("SELECT count(*) FROM raw_email_classifications").fetchone()[0]
