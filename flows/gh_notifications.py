"""
Fetch GitHub notifications and the issues/PRs behind them,
storing results as Prefect table artifacts for later indexing.

Requires a Prefect Secret block named "github-token" containing
a GitHub personal access token with `notifications` scope (read:org optional).

To create the secret:
    prefect python -c "
    from prefect.blocks.system import Secret
    Secret(value='ghp_...').save('github-token', overwrite=True)
    "
"""

import httpx
from datetime import datetime, timezone
from prefect import flow, task, get_run_logger
from prefect.artifacts import create_table_artifact, create_markdown_artifact
from prefect.blocks.system import Secret


GITHUB_API = "https://api.github.com"


def _headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


@task
def load_token() -> str:
    return Secret.load("github-token").get()


@task
def fetch_notifications(token: str, only_unread: bool = True) -> list[dict]:
    logger = get_run_logger()
    params = {"all": str(not only_unread).lower(), "per_page": 50}
    with httpx.Client(headers=_headers(token)) as client:
        resp = client.get(f"{GITHUB_API}/notifications", params=params)
        resp.raise_for_status()
        notifications = resp.json()
    logger.info(f"fetched {len(notifications)} notifications")
    return notifications


@task
def fetch_issue_or_pr(token: str, notification: dict) -> dict | None:
    """Resolve the subject URL to get the actual issue/PR body."""
    subject = notification.get("subject", {})
    url = subject.get("url")
    if not url or subject.get("type") not in ("Issue", "PullRequest"):
        return None
    with httpx.Client(headers=_headers(token)) as client:
        resp = client.get(url)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        data = resp.json()
    return {
        "repo": notification["repository"]["full_name"],
        "type": subject["type"],
        "number": data.get("number"),
        "title": data.get("title"),
        "state": data.get("state"),
        "body": (data.get("body") or "")[:500],  # truncate for artifact display
        "url": data.get("html_url"),
        "updated_at": notification.get("updated_at"),
        "reason": notification.get("reason"),
    }


@flow(name="gh-notifications", log_prints=True)
def gh_notifications(only_unread: bool = True) -> list[dict]:
    """Fetch GitHub notifications and store them as Prefect artifacts."""
    logger = get_run_logger()

    token = load_token()
    notifications = fetch_notifications(token, only_unread=only_unread)

    if not notifications:
        logger.info("no notifications")
        return []

    items = [fetch_issue_or_pr(token, n) for n in notifications]
    items = [i for i in items if i]
    logger.info(f"resolved {len(items)} issues/PRs")

    if not items:
        return []

    # table artifact — one row per notification
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    create_table_artifact(
        key="github-notifications",
        table=items,
        description=f"## GitHub notifications\n\nFetched {len(items)} items at {now}",
    )

    # markdown artifact — readable summary
    lines = [f"## GitHub notifications — {now}\n"]
    for item in items:
        icon = "🔀" if item["type"] == "PullRequest" else "🐛"
        lines.append(
            f"- {icon} **[{item['repo']}#{item['number']}]({item['url']})** "
            f"`{item['state']}` — {item['title']}  \n"
            f"  _reason: {item['reason']}_"
        )
    create_markdown_artifact(
        key="github-notifications-summary",
        markdown="\n".join(lines),
    )

    return items


if __name__ == "__main__":
    gh_notifications()
