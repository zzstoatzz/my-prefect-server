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
from dataclasses import dataclass

import httpx
from prefect import flow, task, get_run_logger
from prefect.blocks.system import Secret
from prefect.cache_policies import CachePolicy
from prefect.context import TaskRunContext


GITHUB_API = "https://api.github.com"


# --- cache policy ---

@dataclass
class ByRepoAndNumber(CachePolicy):
    """Cache key is repo + number only — ignores token/client/other args."""

    def compute_key(
        self,
        task_ctx: TaskRunContext,
        inputs: dict,
        flow_parameters: dict,
        **kwargs,
    ) -> str | None:
        repo = inputs.get("repo")
        number = inputs.get("number")
        if not repo or not number:
            return None
        return f"gh/{repo}/{number}"


# --- tasks ---

@task
def load_token() -> str:
    return Secret.load("github-token").get()


@task
def fetch_notifications(token: str, only_unread: bool = True) -> list[dict]:
    logger = get_run_logger()
    with httpx.Client(headers=_gh_headers(token)) as client:
        resp = client.get(
            f"{GITHUB_API}/notifications",
            params={"all": str(not only_unread).lower(), "per_page": 50},
        )
        resp.raise_for_status()
    notifications = resp.json()
    logger.info(f"fetched {len(notifications)} notifications")
    return notifications


@task(
    cache_policy=ByRepoAndNumber(),
    cache_expiration=datetime.timedelta(hours=24),
    persist_result=True,
    result_serializer="json",
)
def fetch_issue_or_pr(token: str, repo: str, number: int, subject_type: str) -> dict:
    """Fetch a single issue or PR. Cached by repo+number for 24h."""
    with httpx.Client(headers=_gh_headers(token)) as client:
        kind = "pulls" if subject_type == "PullRequest" else "issues"
        resp = client.get(f"{GITHUB_API}/repos/{repo}/{kind}/{number}")
        if resp.status_code == 404:
            return {}
        resp.raise_for_status()
        data = resp.json()
    return {
        "repo": repo,
        "number": number,
        "type": subject_type,
        "title": data.get("title"),
        "state": data.get("state"),
        "body": data.get("body") or "",
        "url": data.get("html_url"),
        "labels": [la["name"] for la in data.get("labels", [])],
        "created_at": data.get("created_at"),
        "updated_at": data.get("updated_at"),
        "user": (data.get("user") or {}).get("login"),
        "comments": data.get("comments", 0),
    }


# --- flow ---

@flow(name="gh-notifications", log_prints=True)
def gh_notifications(only_unread: bool = True) -> list[dict]:
    """
    Fetch GitHub notifications and persist each issue/PR as a cached JSON result.
    Cache hit = skips the fetch entirely. Results on disk = DuckDB-queryable.
    """
    logger = get_run_logger()

    token = load_token()
    notifications = fetch_notifications(token, only_unread=only_unread)

    if not notifications:
        logger.info("no notifications")
        return []

    items = []
    for n in notifications:
        subject = n.get("subject", {})
        subject_type = subject.get("type")
        if subject_type not in ("Issue", "PullRequest"):
            continue

        # parse number from subject URL e.g. .../issues/42
        url = subject.get("url", "")
        try:
            number = int(url.rstrip("/").split("/")[-1])
        except (ValueError, IndexError):
            continue

        repo = n["repository"]["full_name"]
        result = fetch_issue_or_pr(
            token=token,
            repo=repo,
            number=number,
            subject_type=subject_type,
        )
        if result:
            items.append(result)

    logger.info(f"resolved {len(items)} issues/PRs (cache hits skip fetches)")
    return items


# --- helpers ---

def _gh_headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


if __name__ == "__main__":
    gh_notifications()
