"""
Fetch GitHub notifications and tangled.org items, persist both to DuckDB.

Combines the two data sources into one flow so DuckDB's single-writer lock
is never contested — both persists happen sequentially in the same process.

Cache policy: each GitHub issue is cached by repo+number for 24h.

Requires:
  - Secret block "github-token" (notifications scope)
  - PREFECT_LOCAL_STORAGE_PATH env var pointing at the mounted PVC
"""

import datetime
import os
from dataclasses import dataclass

import httpx
from prefect import flow, get_run_logger, task, unmapped
from prefect.blocks.system import Secret
from prefect.cache_policies import CachePolicy
from prefect.context import TaskRunContext

from mps.db import write_github_issues, write_tangled_items
from mps.github import IssueOrPR, IssueRef, gh_headers
from mps.tangled import PDS_BASE, TangledItem, fetch_items, fetch_repo_at_uris

GITHUB_API = "https://api.github.com"

TANGLED_COLLECTIONS = [
    "sh.tangled.repo.issue",
    "sh.tangled.repo.pull",
    "sh.tangled.repo.issue.comment",
    "sh.tangled.repo.pull.comment",
]

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


# --- github tasks ---


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
def fetch_authored_items(token: str, username: str = "zzstoatzz") -> list[IssueRef]:
    """Fetch open issues/PRs authored by the user via the search API."""
    logger = get_run_logger()
    with httpx.Client(headers=gh_headers(token)) as client:
        resp = client.get(
            f"{GITHUB_API}/search/issues",
            params={"q": f"author:{username} is:open", "per_page": 50, "sort": "updated"},
        )
        resp.raise_for_status()

    refs: list[IssueRef] = []
    for item in resp.json().get("items", []):
        html_url = item.get("html_url", "")
        is_pr = "/pull/" in html_url
        parts = html_url.split("/")
        try:
            repo = f"{parts[3]}/{parts[4]}"
            number = int(parts[-1])
        except (IndexError, ValueError):
            continue
        refs.append(IssueRef(
            repo=repo,
            number=number,
            subject_type="PullRequest" if is_pr else "Issue",
        ))

    logger.info(f"fetched {len(refs)} authored items for {username}")
    return refs


# --- tangled tasks ---


@task
def fetch_all_tangled_items() -> list[TangledItem]:
    """Fetch issues, PRs, and comments from the tangled.org PDS."""
    logger = get_run_logger()
    with httpx.Client(base_url=PDS_BASE, timeout=30) as client:
        repo_uris = fetch_repo_at_uris(client)
        logger.info(f"found {len(repo_uris)} target repos on PDS")

        items: list[TangledItem] = []
        for collection in TANGLED_COLLECTIONS:
            batch = fetch_items(client, collection, repo_uris)
            logger.info(f"{collection}: {len(batch)} records")
            items.extend(batch)

    return items


# --- persist tasks ---


def _db_path() -> str:
    return os.environ.get(
        "ANALYTICS_DB_PATH",
        os.environ.get("PREFECT_LOCAL_STORAGE_PATH", "/tmp") + "/analytics.duckdb",
    )


@task
def persist_github(items: list[IssueOrPR]) -> int:
    return write_github_issues(items, _db_path())


@task
def persist_tangled(items: list[TangledItem]) -> int:
    return write_tangled_items(items, _db_path())


# --- flow ---


@flow(name="ingest", log_prints=True)
def ingest(only_unread: bool = True):
    """
    Fetch GitHub and tangled.org data concurrently, then persist sequentially.
    """
    logger = get_run_logger()

    token = load_token()

    # kick off tangled fetch immediately (no deps)
    tangled_future = fetch_all_tangled_items.submit()

    # github fetches need the token
    notif_refs = fetch_notifications(token, only_unread=only_unread)
    authored_refs = fetch_authored_items(token)

    # merge and dedupe by (repo, number)
    seen: set[tuple[str, int]] = set()
    refs: list[IssueRef] = []
    for ref in notif_refs + authored_refs:
        key = (ref.repo, ref.number)
        if key not in seen:
            seen.add(key)
            refs.append(ref)

    # fetch full issue/PR details (cached)
    gh_items: list[IssueOrPR] = []
    if refs:
        futures = fetch_issue_or_pr.map(refs, unmapped(token))
        gh_items = [r for r in futures.result() if r is not None]
    logger.info(f"resolved {len(gh_items)} github issues/PRs")

    # wait for tangled fetch
    tangled_items = tangled_future.result()
    logger.info(f"fetched {len(tangled_items)} tangled items")

    # sequential writes — same process, no DuckDB lock contention
    if gh_items:
        total = persist_github(gh_items)
        logger.info(f"upserted {len(gh_items)} github rows; {total} total in raw_github_issues")

    if tangled_items:
        total = persist_tangled(tangled_items)
        logger.info(f"persisted {len(tangled_items)} tangled rows; {total} total in raw_tangled_items")


if __name__ == "__main__":
    ingest()
