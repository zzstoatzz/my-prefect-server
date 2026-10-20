from __future__ import annotations

import re
import time


def batch_branch(
    policy: dict, issue_number: int, published_count: int = 0, now: int | None = None
) -> str:
    timestamp = int(time.time()) if now is None else now
    identifier = policy.get("id", "")
    if not isinstance(identifier, str) or re.fullmatch(r"[a-z0-9-]{1,50}", identifier) is None:
        raise ValueError("Invalid batch identifier")
    for key in ("started_at", "expires_at", "max_prs"):
        if type(policy.get(key)) is not int:
            raise ValueError(f"Invalid batch {key}")
    if not 1 <= policy["max_prs"] <= 3:
        raise ValueError("A batch may open at most three drafts")
    if not policy["started_at"] <= timestamp < policy["expires_at"] <= policy["started_at"] + 7200:
        raise ValueError("Batch publication window is expired, future, or longer than two hours")
    issues = policy.get("issue_numbers")
    if (
        not isinstance(issues, list)
        or not 1 <= len(issues) <= 12
        or any(type(number) is not int or number <= 0 for number in issues)
        or len(set(issues)) != len(issues)
        or issue_number not in issues
    ):
        raise ValueError("Issue is outside the bounded batch")
    if published_count >= policy["max_prs"]:
        raise ValueError("Batch draft limit reached")
    return f"agentic/{identifier}/issue-{issue_number}"
