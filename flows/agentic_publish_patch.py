from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import subprocess
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

from prefect import flow, get_run_logger, task
from prefect.artifacts import create_link_artifact
from prefect.blocks.system import Secret
from prefect.client.orchestration import get_client
from prefect.context import get_run_context

from flows.agentic_batch_policy import batch_branch
from flows.agentic_validate_patch import fetch_source, install_tools, run_command

REPOSITORY = "PrefectHQ/fastmcp"
VALIDATOR = UUID("5f1e86fb-d975-47e5-a85d-a45b3dd04865")


@task(retries=3, retry_delay_seconds=[2, 5, 10], retry_jitter_factor=1)
def github(path: str, method: str = "GET", body: dict | None = None) -> dict | list | None:
    token = Secret.load("fastmcp-triage-gh-token").get()
    request = urllib.request.Request(
        "https://api.github.com/" + path,
        data=json.dumps(body).encode() if body is not None else None,
        headers={
            "Authorization": "Bearer " + token,
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
        },
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        if error.code == 404 and method == "GET":
            return None
        raise


def pages(path: str) -> list[dict]:
    result = []
    for page in range(1, 101):
        separator = "&" if "?" in path else "?"
        batch = github(f"{path}{separator}per_page=100&page={page}")
        if not isinstance(batch, list):
            raise ValueError("Expected a paginated GitHub list")
        result.extend(batch)
        if len(batch) < 100:
            return result
    raise ValueError("GitHub pagination exceeded the publication bound")


def check_review(issue: dict, timeline: list[dict], reviewed_at: str) -> None:
    reviewed = datetime.fromisoformat(reviewed_at.replace("Z", "+00:00"))
    now = datetime.now(UTC)
    if reviewed > now or now - reviewed > timedelta(hours=1):
        raise ValueError("Publication requires a review from the last hour")
    if issue["state"] != "open" or issue["assignees"]:
        raise ValueError("Issue is closed or assigned")
    updates = [issue["updated_at"]] + [
        event["created_at"] for event in timeline if event.get("created_at")
    ]
    if any(datetime.fromisoformat(value.replace("Z", "+00:00")) > reviewed for value in updates):
        raise ValueError("Issue changed after the agent reviewed it")


@task(retries=3, retry_delay_seconds=[2, 5, 10], retry_jitter_factor=1)
def validated_patch(validation_run_id: str, base_sha: str, patch_sha256: str) -> str:
    with get_client(sync_client=True) as client:
        run = client.read_flow_run(UUID(validation_run_id))
    if run.deployment_id != VALIDATOR or not run.state or not run.state.is_completed():
        raise ValueError("Expected a completed run of the designated validation deployment")
    patch = run.parameters["patch"]
    if (
        run.parameters["base_sha"] != base_sha
        or hashlib.sha256(patch.encode()).hexdigest() != patch_sha256
    ):
        raise ValueError("Review does not identify the validated patch")
    return patch


@task(retries=3, retry_delay_seconds=[2, 5, 10], retry_jitter_factor=1)
def reviewed_patch(review_run_id: str, approval: dict) -> str:
    with get_client(sync_client=True) as client:
        parent = client.read_flow_run(UUID(review_run_id))
        if str(parent.flow_id) not in {
            "aa69d171-a0a1-40f7-9679-4b811550ad26",
            "6afddacc-ee55-4190-a5dc-13704bf5ddd2",
        }:
            raise ValueError("Expected a designated Agentic Workflow review")
        prefix = f"/flow_runs/{review_run_id}/execution-plan"
        graph = client.request("GET", prefix).json()
        review = next(node for node in graph["nodes"] if node["node"] == "review")
        if review["status"] != "completed":
            raise ValueError("Agent review has not completed")
        approved = client.request(
            "GET", f"{prefix}/activations/{review['activation_id']}/outputs/approved"
        ).json()
        if any(approved.get(key) != value for key, value in approval.items()):
            raise ValueError("Publication metadata differs from the stored approval")

        def input_value(name: str) -> dict:
            edge = next(
                edge
                for edge in graph["edges"]
                if edge["target"] == {"node": "review", "input": name}
            )
            source = edge["source"]
            if source["type"] == "plan_input":
                return parent.parameters[source["input"]]
            node = next(node for node in graph["nodes"] if node["node"] == source["node"])
            return client.request(
                "GET", f"{prefix}/activations/{node['activation_id']}/outputs/{source['output']}"
            ).json()

        patch_input = input_value("patch")
        validation = input_value("validation")
        candidate = input_value("candidate")
    if (
        validation["child_flow_run_id"] != approval["validation_run_id"]
        or candidate["issue_number"] != approval["issue_number"]
        or patch_input["base_sha"] != approval["base_sha"]
    ):
        raise ValueError("Approval references different workflow inputs")
    return validated_patch.fn(
        approval["validation_run_id"],
        approval["base_sha"],
        hashlib.sha256(patch_input["patch"].encode()).hexdigest(),
    )


def references_issue(body: str, issue_number: int) -> bool:
    full = rf"https://(?:redirect\.)?github\.com/{REPOSITORY}/issues/{issue_number}(?!\d)"
    if re.search(full, body, re.IGNORECASE):
        return True
    text = re.sub(r"<a\b[^>]*>.*?</a>", "", body, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"\[[^\]]*\]\([^)]+\)", "", text)
    return (
        re.search(rf"(?<![\w/])(?:{REPOSITORY})?#{issue_number}(?!\d)", text, re.IGNORECASE)
        is not None
    )


@task(retries=3, retry_delay_seconds=[2, 5, 10], retry_jitter_factor=1)
def publication_batch(review_run_id: str, issue_number: int) -> dict:
    with get_client(sync_client=True) as client:
        run = client.read_flow_run(UUID(review_run_id))
    policy = run.parameters.get("batch", {})
    batch_branch(policy, issue_number)
    return policy


@task(retries=3, retry_delay_seconds=[2, 5, 10], retry_jitter_factor=1)
def check_batch_capacity(policy: dict, issue_number: int) -> str:
    branch = batch_branch(policy, issue_number)
    prefix = f"agentic/{policy['id']}/"
    count = 0
    for page in range(1, 11):
        prs = github.fn(
            f"repos/{REPOSITORY}/pulls?state=all&sort=created&direction=desc&per_page=100&page={page}"
        )
        if not isinstance(prs, list):
            raise ValueError("Cannot establish batch publication count")
        count += sum(pr["head"]["ref"].startswith(prefix) for pr in prs)
        if (
            len(prs) < 100
            or datetime.fromisoformat(prs[-1]["created_at"].replace("Z", "+00:00")).timestamp()
            < policy["started_at"]
        ):
            batch_branch(policy, issue_number, count)
            return branch
    raise ValueError("Batch publication inventory exceeded its bound")


def check_competing_work(
    issue_number: int, branch: str, reviewed_at: str
) -> tuple[dict, list[dict]]:
    issue = github(f"repos/{REPOSITORY}/issues/{issue_number}")
    if not isinstance(issue, dict):
        raise ValueError("Issue does not exist")
    timeline = pages(f"repos/{REPOSITORY}/issues/{issue_number}/timeline")
    own = pages(f"repos/{REPOSITORY}/pulls?state=all&head=PrefectHQ:{branch}")
    if not own:
        check_review(issue, timeline, reviewed_at)
    query = urllib.parse.quote(f"repo:{REPOSITORY} is:pr {issue_number} in:body")
    matches = github(f"search/issues?q={query}&per_page=100")
    if (
        not isinstance(matches, dict)
        or matches.get("incomplete_results")
        or matches["total_count"] > 100
    ):
        raise ValueError("Could not exhaust corresponding PR search")
    own_numbers = {pr["number"] for pr in own}
    for match in matches["items"]:
        if match["number"] not in own_numbers and references_issue(
            match.get("body") or "", issue_number
        ):
            raise ValueError(f"Corresponding PR already exists: {match['html_url']}")
    for event in timeline:
        source = event.get("source", {}).get("issue", {})
        if source.get("pull_request") and source.get("number") not in own_numbers:
            raise ValueError("Issue timeline contains another PR")
    return issue, own


@task(retries=3, retry_delay_seconds=[2, 5, 10], retry_jitter_factor=1)
def push_branch(repository: Path, branch: str) -> None:
    token = Secret.load("fastmcp-triage-gh-token").get()
    environment = dict(os.environ)
    authorization = base64.b64encode(("x-access-token:" + token).encode()).decode()
    environment.update(
        GIT_CONFIG_COUNT="1",
        GIT_CONFIG_KEY_0="http.https://github.com/.extraheader",
        GIT_CONFIG_VALUE_0="AUTHORIZATION: basic " + authorization,
    )
    subprocess.run(
        ["git", "push", "origin", f"HEAD:refs/heads/{branch}"],
        cwd=repository,
        env=environment,
        check=True,
    )


@task(retries=3, retry_delay_seconds=[2, 5, 10], retry_jitter_factor=1)
def create_draft(branch: str, head: str, title: str, body: str) -> dict:
    existing = pages(f"repos/{REPOSITORY}/pulls?state=all&head=PrefectHQ:{branch}")
    if existing:
        if len(existing) != 1 or existing[0]["head"]["sha"] != head:
            raise ValueError("Unexpected PR exists for this publication branch")
        return existing[0]
    return github.fn(
        f"repos/{REPOSITORY}/pulls",
        "POST",
        {"title": title, "body": body, "head": branch, "base": "main", "draft": True},
    )


@flow(name="fastmcp-publish-validated-patch", timeout_seconds=1200)
def publish_patch(
    issue_number: int,
    validation_run_id: str,
    base_sha: str,
    reviewed_at: str,
    title: str,
    body: str,
    dry_run: bool = False,
    recovery_review_run_id: str | None = None,
) -> dict[str, str]:
    if issue_number <= 0 or not title.strip() or "\n" in title or not body.strip():
        raise ValueError("Expected an issue number, one-line title and PR body")
    if recovery_review_run_id is None:
        parent_task = get_run_context().flow_run.parent_task_run_id
        if parent_task is None:
            raise ValueError("Publication must follow an Agentic Workflow review")
        with get_client(sync_client=True) as client:
            recovery_review_run_id = str(client.read_task_run(parent_task).flow_run_id)
    patch = reviewed_patch(
        recovery_review_run_id,
        {
            "issue_number": issue_number,
            "validation_run_id": validation_run_id,
            "base_sha": base_sha,
            "reviewed_at": reviewed_at,
            "title": title,
            "body": body,
        },
    )
    policy = publication_batch(recovery_review_run_id, issue_number)
    branch = check_batch_capacity(policy, issue_number)
    issue, existing = check_competing_work(issue_number, branch, reviewed_at)
    reporter = github(f"users/{issue['user']['login']}")
    actor = github("user")
    credit = f"Co-authored-by: {reporter['login']} <{reporter['id']}+{reporter['login']}@users.noreply.github.com>"
    pr_body = (
        body.rstrip()
        + f"\n\nPrepared and reviewed with Prefect Cloud Agentic Workflows.\n\n{credit}\n"
    )
    with tempfile.TemporaryDirectory(prefix="fastmcp-publication-") as temporary:
        root = Path(temporary)
        install_tools(root / "tools")
        repository = root / "fastmcp"
        fetch_source(repository, base_sha)
        run_command(["git", "switch", "-c", branch], repository)
        patch_file = root / "candidate.patch"
        patch_file.write_text(patch)
        run_command(["git", "apply", "--recount", str(patch_file)], repository)
        run_command(["git", "add", "."], repository)
        run_command(["git", "diff", "--cached", "--check"], repository)
        tree = subprocess.check_output(["git", "write-tree"], cwd=repository).decode().strip()
        remote = github(f"repos/{REPOSITORY}/git/ref/heads/{branch}")
        if remote:
            head = remote["object"]["sha"]
            commit = github(f"repos/{REPOSITORY}/git/commits/{head}")
            if commit["tree"]["sha"] != tree or [parent["sha"] for parent in commit["parents"]] != [
                base_sha
            ]:
                raise ValueError("Existing branch does not contain this validated change")
            if credit not in commit["message"]:
                raise ValueError("Existing branch lacks required contributor credit")
        else:
            if existing:
                raise ValueError("Prior PR exists but its branch is missing")
            run_command(["uv", "sync"], repository)
            run_command(["uv", "run", "prek", "install"], repository)
            run_command(["git", "config", "user.name", actor["login"]], repository)
            run_command(
                [
                    "git",
                    "config",
                    "user.email",
                    f"{actor['id']}+{actor['login']}@users.noreply.github.com",
                ],
                repository,
            )
            message = root / "commit-message.txt"
            message.write_text(title.strip() + "\n\n" + credit + "\n")
            run_command(["git", "commit", "--file", str(message)], repository)
            if subprocess.check_output(["git", "status", "--porcelain"], cwd=repository).strip():
                raise ValueError("Commit hooks modified the validated tree")
            if (
                subprocess.check_output(["git", "rev-parse", "HEAD^{tree}"], cwd=repository)
                .decode()
                .strip()
                != tree
            ):
                raise ValueError("Committed tree differs from validation")
            head = (
                subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repository)
                .decode()
                .strip()
            )
        _, existing = check_competing_work(issue_number, branch, reviewed_at)
        if existing:
            if len(existing) != 1 or existing[0]["head"]["sha"] != head:
                raise ValueError("Existing PR differs from validated publication")
            return {"status": "existing_pr", "pr_url": existing[0]["html_url"], "head_sha": head}
        main = github(f"repos/{REPOSITORY}/git/ref/heads/main")
        if main["object"]["sha"] != base_sha:
            raise ValueError("Main changed since validation")
        if dry_run:
            return {"status": "dry_run", "head_sha": head}
        check_batch_capacity(policy, issue_number)
        if not remote:
            push_branch(repository, branch)
        _, existing = check_competing_work(issue_number, branch, reviewed_at)
        if existing:
            raise ValueError("PR appeared during publication; inspect before retrying")
        check_batch_capacity(policy, issue_number)
        pr = create_draft(branch, head, title, pr_body)
        if not pr["draft"] or pr["head"]["sha"] != head:
            raise ValueError("Created PR does not match the expected draft/head")
        create_link_artifact(
            key="fastmcp-agentic-draft-pr",
            link=pr["html_url"],
            description=f"Validated draft for #{issue_number}; head {head}",
        )
        get_run_logger().info("Draft PR: %s (head=%s)", pr["html_url"], head)
        return {"status": "draft_pr", "pr_url": pr["html_url"], "head_sha": head}
