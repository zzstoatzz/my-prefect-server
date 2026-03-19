"""
Fetch GitHub notifications and the issues/PRs behind them.
Results are persisted as JSON to a shared PVC so DuckDB can query them later.

Cache policy: each issue is cached by repo+number for 24h — we don't re-fetch
what we already have.

Requires:
  - Secret block "github-token" (notifications scope)
  - PREFECT_LOCAL_STORAGE_PATH env var pointing at the mounted PVC
    (set in the work pool base job template or via deploy/results-pvc.yaml)
"""

# k8s: prefect uses system python, but uv sync in the pull step creates .venv/
# with extra deps (duckdb, etc). inject it into sys.path before any imports.
import sys as _sys
from pathlib import Path as _Path
_site = _Path(__file__).parent.parent / ".venv" / "lib" / f"python{_sys.version_info.major}.{_sys.version_info.minor}" / "site-packages"
if _site.exists() and str(_site) not in _sys.path:
    _sys.path.insert(0, str(_site))

import datetime
import os
from dataclasses import dataclass
from typing import Literal

import httpx
from pydantic import BaseModel
from prefect import flow, task, get_run_logger, unmapped
from prefect.blocks.system import Secret
from prefect.cache_policies import CachePolicy
from prefect.context import TaskRunContext


GITHUB_API = "https://api.github.com"


# --- models ---

class IssueRef(BaseModel):
    """Parsed from a GitHub notification — what we need to fetch."""
    repo: str
    number: int
    subject_type: Literal["Issue", "PullRequest"]


class IssueOrPR(BaseModel):
    """Fetched issue or PR data — persisted as JSON result."""
    repo: str
    number: int
    type: str
    title: str | None = None
    state: str | None = None
    body: str = ""
    url: str | None = None
    labels: list[str] = []
    created_at: str | None = None
    updated_at: str | None = None
    user: str | None = None
    comments: int = 0
    reactions_total: int = 0


# --- cache policy ---

# bump to invalidate all cached results (e.g. when fetch shape changes)
_CACHE_VERSION = "v2"


@dataclass
class ByRepoAndNumber(CachePolicy):
    """Cache key is repo + number only — ignores token and other args."""

    def compute_key(
        self,
        task_ctx: TaskRunContext,
        inputs: dict,
        flow_parameters: dict,
        **kwargs,
    ) -> str | None:
        ref: IssueRef | None = inputs.get("ref")
        if ref is None:
            return None
        return f"gh/{_CACHE_VERSION}/{ref.repo}/{ref.number}"


# --- tasks ---

@task
def load_token() -> str:
    return Secret.load("github-token").get()


@task
def fetch_notifications(token: str, only_unread: bool = True) -> list[IssueRef]:
    """Fetch notifications and parse into IssueRef objects (Issues/PRs only)."""
    logger = get_run_logger()
    with httpx.Client(headers=_gh_headers(token)) as client:
        resp = client.get(
            f"{GITHUB_API}/notifications",
            params={"all": str(not only_unread).lower(), "per_page": 50},
        )
        resp.raise_for_status()

    refs: list[IssueRef] = []
    for n in resp.json():
        subject = n.get("subject", {})
        subject_type = subject.get("type")
        if subject_type not in ("Issue", "PullRequest"):
            continue
        url = subject.get("url", "")
        try:
            number = int(url.rstrip("/").split("/")[-1])
        except (ValueError, IndexError):
            continue
        refs.append(IssueRef(
            repo=n["repository"]["full_name"],
            number=number,
            subject_type=subject_type,
        ))

    logger.info(f"fetched {len(refs)} issue/PR notifications")
    return refs


@task(
    cache_policy=ByRepoAndNumber(),
    cache_expiration=datetime.timedelta(hours=24),
    persist_result=True,
    result_serializer="json",
)
def fetch_issue_or_pr(ref: IssueRef, token: str) -> IssueOrPR | None:
    """Fetch a single issue or PR. Cached by repo+number for 24h."""
    with httpx.Client(headers=_gh_headers(token)) as client:
        # always use the issues endpoint — PRs are issues in GitHub's model and
        # only the issues endpoint returns the reactions field
        resp = client.get(f"{GITHUB_API}/repos/{ref.repo}/issues/{ref.number}")
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        data = resp.json()

    return IssueOrPR(
        repo=ref.repo,
        number=ref.number,
        type=ref.subject_type,
        title=data.get("title"),
        state=data.get("state"),
        body=data.get("body") or "",
        url=data.get("html_url"),
        labels=[la["name"] for la in data.get("labels", [])],
        created_at=data.get("created_at"),
        updated_at=data.get("updated_at"),
        user=(data.get("user") or {}).get("login"),
        comments=data.get("comments", 0),
        reactions_total=(data.get("reactions") or {}).get("total_count", 0),
    )


@task
def write_to_duckdb(items: list[IssueOrPR], db_path: str) -> int:
    import duckdb
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


# --- flow ---

@flow(name="gh-notifications", log_prints=True)
def gh_notifications(only_unread: bool = True) -> list[IssueOrPR]:
    """
    Fetch GitHub notifications and persist each issue/PR as a cached JSON result.
    Cache hit = skips the fetch entirely. Results on disk = DuckDB-queryable.
    """
    logger = get_run_logger()

    token = load_token()
    refs = fetch_notifications(token, only_unread=only_unread)

    if not refs:
        logger.info("no notifications")
        return []

    futures = fetch_issue_or_pr.map(refs, unmapped(token))
    items = [r for r in futures.result() if r is not None]
    logger.info(f"resolved {len(items)} issues/PRs")

    db_path = os.environ.get(
        "ANALYTICS_DB_PATH",
        os.environ.get("PREFECT_LOCAL_STORAGE_PATH", "/tmp") + "/analytics.duckdb",
    )
    total = write_to_duckdb(items, db_path)
    logger.info(f"upserted {len(items)} rows; {total} total in raw_github_issues")
    return items


# --- helpers ---

def _gh_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


if __name__ == "__main__":
    gh_notifications()
