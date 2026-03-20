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

import datetime
import os
from dataclasses import dataclass

import httpx
from prefect import flow, task, get_run_logger, unmapped
from prefect.blocks.system import Secret
from prefect.cache_policies import CachePolicy
from prefect.context import TaskRunContext

from mps.db import write_github_issues
from mps.github import IssueOrPR, IssueRef, gh_headers

GITHUB_API = "https://api.github.com"

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


@task
def load_token() -> str:
    return Secret.load("github-token").get()


@task
def fetch_notifications(token: str, only_unread: bool = True) -> list[IssueRef]:
    """Fetch notifications and parse into IssueRef objects (Issues/PRs only)."""
    logger = get_run_logger()
    with httpx.Client(headers=gh_headers(token)) as client:
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
    with httpx.Client(headers=gh_headers(token)) as client:
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
def persist_to_duckdb(items: list[IssueOrPR]) -> int:
    db_path = os.environ.get(
        "ANALYTICS_DB_PATH",
        os.environ.get("PREFECT_LOCAL_STORAGE_PATH", "/tmp") + "/analytics.duckdb",
    )
    return write_github_issues(items, db_path)


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

    total = persist_to_duckdb(items)
    logger.info(f"upserted {len(items)} rows; {total} total in raw_github_issues")
    return items


if __name__ == "__main__":
    gh_notifications()
