"""DuckDB write helpers shared across flows."""

import datetime

import duckdb

from mps.phi import PhiInteraction, PhiObservation


def write_github_issues(items: list, db_path: str) -> int:
    """Upsert IssueOrPR items into raw_github_issues. Returns total row count."""
    con = duckdb.connect(db_path)
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
            item.repo, item.number, item.type,
            item.title, item.state, item.body, item.url,
            item.labels, item.created_at, item.updated_at,
            item.user, item.comments, item.reactions_total,
            datetime.datetime.now(datetime.UTC),
        )
        for item in items
    ]
    con.executemany(
        "INSERT OR REPLACE INTO raw_github_issues VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    count = con.execute("SELECT count(*) FROM raw_github_issues").fetchone()[0]
    con.close()
    return count


def write_tangled_items(items: list, db_path: str) -> int:
    """Upsert TangledItem objects into raw_tangled_items. Returns total row count."""
    con = duckdb.connect(db_path)
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
            item.repo, item.kind, item.title,
            item.body, item.url, item.at_uri,
            item.author_did, item.author_handle,
            item.created_at, item.parent_uri,
            datetime.datetime.now(datetime.UTC),
        )
        for item in items
    ]
    if rows:
        con.executemany(
            "INSERT OR REPLACE INTO raw_tangled_items VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
    count = con.execute("SELECT count(*) FROM raw_tangled_items").fetchone()[0]
    con.close()
    return count


def write_phi_observations(items: list[PhiObservation], db_path: str) -> int:
    """Upsert phi observations into raw_phi_observations. Returns total row count."""
    con = duckdb.connect(db_path)
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
            item.handle, item.observation_id,
            item.content, item.tags,
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
    count = con.execute("SELECT count(*) FROM raw_phi_observations").fetchone()[0]
    con.close()
    return count


def write_phi_interactions(items: list[PhiInteraction], db_path: str) -> int:
    """Upsert phi interactions into raw_phi_interactions. Returns total row count."""
    con = duckdb.connect(db_path)
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
            item.handle, item.interaction_id,
            item.content, item.created_at,
            datetime.datetime.now(datetime.UTC),
        )
        for item in items
    ]
    if rows:
        con.executemany(
            "INSERT OR REPLACE INTO raw_phi_interactions VALUES (?, ?, ?, ?, ?)",
            rows,
        )
    count = con.execute("SELECT count(*) FROM raw_phi_interactions").fetchone()[0]
    con.close()
    return count
