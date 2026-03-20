"""DuckDB write helpers shared across flows."""

import datetime

import duckdb


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
