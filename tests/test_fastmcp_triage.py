from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from flows import fastmcp_triage
from flows.fastmcp_triage import (
    TriageResult,
    branch_for,
    claude_argv,
    latest_by_number,
    leaks,
    needs_triage,
    render_receipt,
    render_summary,
    sandbox_settings,
    triaged_version_from,
)


def _result(**overrides) -> TriageResult:
    fields = {
        "action": "draft",
        "urgency": "routine",
        "verdict": "real bug in openapi error logging",
        "rationale": "a compatibility question about log levels remains",
        "pr_title": "openapi: log upstream http errors without a traceback",
        "pr_body": "body",
        "branch": "fix/openapi-error-logging",
    }
    return TriageResult(**{**fields, **overrides})


def _event(*items: dict) -> dict:
    return {"payload": {"surfaced": list(items)}}


# --- what to look at ---------------------------------------------------------


def test_latest_by_number_keeps_the_newest_version_of_each_thread():
    events = [
        _event({"number": 5280, "updated_at": "2026-09-25T20:43:53Z"}),
        _event(
            {"number": 5280, "updated_at": "2026-09-26T04:06:12Z"},
            {"number": 5269, "updated_at": "a"},
        ),
        {"payload": {"brief": "a brief from before `surfaced` existed"}},
    ]
    assert latest_by_number(events) == [
        {"number": 5269, "updated_at": "a"},
        {"number": 5280, "updated_at": "2026-09-26T04:06:12Z"},
    ]


@pytest.mark.parametrize(
    ("context", "triaged", "expected"),
    [
        ({"state": "open", "updated_at": "v2"}, None, True),
        ({"state": "open", "updated_at": "v2"}, "v1", True),
        ({"state": "open", "updated_at": "v2"}, "v2", False),
        ({"state": "closed", "updated_at": "v2"}, None, False),
        ({"state": "open", "merged": True, "updated_at": "v2"}, None, False),
    ],
)
def test_needs_triage(context, triaged, expected):
    assert needs_triage(context, triaged) is expected


def test_the_receipt_records_the_version_it_triaged():
    context = {"number": 5280, "url": "u", "title": "t", "updated_at": "2026-09-26T04:06:12Z"}
    agent = {"result": _result(), "session_id": "s", "cost_usd": 1.5}
    markdown = render_receipt(context, agent, {"pr": "https://github.com/x/pull/1", "draft": True})
    assert triaged_version_from(markdown) == "2026-09-26T04:06:12Z"
    assert triaged_version_from(None) is None
    assert triaged_version_from("no receipt header") is None


# --- the boundary ------------------------------------------------------------


def test_the_agent_cannot_reach_github():
    # the whole publishing boundary rests on this: with no route to GitHub,
    # nothing an issue says can make the agent push, comment, or open anything
    settings = sandbox_settings()
    domains = settings["sandbox"]["network"]["allowedDomains"]
    assert not [d for d in domains if "github" in d]
    # the fastmcp suite binds localhost ports; that must not widen egress
    assert settings["sandbox"]["network"]["allowLocalBinding"] is True
    assert settings["sandbox"]["allowUnsandboxedCommands"] is False


def test_the_agent_cannot_read_home_or_write_git_and_claude_config():
    settings = sandbox_settings()
    fs = settings["sandbox"]["filesystem"]
    assert "~/" in fs["denyRead"]
    assert {"./.git", "./.claude"} <= set(fs["denyWrite"])
    allow = settings["permissions"]["allow"]
    assert "Read" not in allow and "Read(./**)" in allow
    assert {"WebFetch", "WebSearch"} <= set(settings["permissions"]["deny"])


def test_the_agent_runs_without_user_settings_or_mcp_servers(tmp_path):
    argv = claude_argv(tmp_path / "settings.json")
    assert argv[argv.index("--setting-sources") + 1] == "project"
    assert argv[argv.index("--permission-mode") + 1] == "dontAsk"
    assert "--strict-mcp-config" in argv
    schema = json.loads(argv[argv.index("--json-schema") + 1])
    assert set(schema["properties"]["action"]["enum"]) == {"none", "draft", "ready"}


def test_leaks_names_the_problem_without_echoing_the_value():
    token = "gho_" + "a" * 36
    problems = leaks(f"diff with {token} inside", [token])
    assert problems and all(token not in p for p in problems)
    assert leaks("ordinary diff", ["gho_" + "b" * 36]) == []
    assert leaks("AKIA" + "A" * 16, []) == ["contains a token-shaped string"]


@pytest.mark.parametrize(
    ("branch", "expected"),
    [
        ("fix/openapi-error-logging", "fix/openapi-error-logging"),
        ("main", "fix/issue-5280"),
        ("fix/../../main", "fix/issue-5280"),
        ("feature/x", "fix/issue-5280"),
        ("", "fix/issue-5280"),
    ],
)
def test_branch_names_are_constrained(branch, expected):
    assert branch_for(_result(branch=branch), 5280) == expected


def test_summary_fits_discord_and_marks_ship_now():
    outcome = {
        "number": 5280,
        "url": "https://github.com/PrefectHQ/fastmcp/issues/5280",
        "severity": "bug",
        "agent": {"result": _result(urgency="ship-now"), "session_id": "s"},
        "published": {"pr": "https://github.com/PrefectHQ/fastmcp/pull/9", "draft": False},
    }
    body = render_summary([outcome] * 40)
    assert len(body) <= fastmcp_triage.SUMMARY_CHAR_BUDGET
    assert body.startswith("🚀")
    blocked = {**outcome, "published": {"blocked": ["contains a token-shaped string"]}}
    assert "publish blocked" in render_summary([blocked])


# --- publishing --------------------------------------------------------------


def test_a_review_of_someone_elses_pull_request_never_opens_one(tmp_path):
    context = {"number": 5269, "kind": "pull_request"}
    out = fastmcp_triage.publish.fn(context, tmp_path, {"result": _result(action="ready")}, "run")
    assert "pr" not in out


def _repo_with_remote(tmp_path: Path) -> Path:
    remote = tmp_path / "remote.git"
    clone = tmp_path / "run" / "clone"
    subprocess.run(["git", "init", "--quiet", "--bare", str(remote)], check=True)
    subprocess.run(["git", "init", "--quiet", "-b", "main", str(clone)], check=True)
    (clone / "README.md").write_text("fastmcp\n")
    subprocess.run(["git", "-C", str(clone), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(clone), "commit", "--quiet", "-m", "init"], check=True)
    subprocess.run(["git", "-C", str(clone), "remote", "add", "origin", str(remote)], check=True)
    return clone


@pytest.fixture
def git_identity(tmp_path, monkeypatch):
    config = tmp_path / "gitconfig"
    config.write_text("[user]\n\tname = nate\n\temail = nate@example.test\n")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))


def test_publish_commits_and_opens_a_draft_without_running_repo_hooks(
    tmp_path, monkeypatch, git_identity
):
    clone = _repo_with_remote(tmp_path)
    marker = tmp_path / "hook-ran"
    for hook in ("pre-commit", "pre-push", "post-checkout"):
        path = clone / ".git" / "hooks" / hook
        path.write_text(f"#!/bin/sh\ntouch {marker}\n")
        path.chmod(0o755)
    (clone / "fix.py").write_text("x = 1\n")

    calls: list[tuple[str, ...]] = []

    def fake_gh(*args: str) -> str:
        calls.append(args)
        return (
            "gho_" + "z" * 36
            if args[:2] == ("auth", "token")
            else "https://github.com/PrefectHQ/fastmcp/pull/9\n"
        )

    monkeypatch.setattr(fastmcp_triage, "_gh", fake_gh)
    context = {"number": 5280, "kind": "issue"}
    out = fastmcp_triage.publish.fn(
        context, clone, {"result": _result(), "session_id": "s"}, "run-url"
    )

    assert out == {"pr": "https://github.com/PrefectHQ/fastmcp/pull/9", "draft": True}
    assert not marker.exists(), "a hook in the agent's clone ran during publish"
    create = next(c for c in calls if c[:2] == ("pr", "create"))
    assert "--draft" in create and "fix/openapi-error-logging" in create
    pushed = subprocess.run(
        ["git", "-C", str(tmp_path / "remote.git"), "branch", "--list"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert "fix/openapi-error-logging" in pushed


def test_publish_refuses_a_change_carrying_the_operators_token(tmp_path, monkeypatch, git_identity):
    clone = _repo_with_remote(tmp_path)
    token = "gho_" + "q" * 36
    (clone / "leak.py").write_text(f"TOKEN = '{token}'\n")
    calls: list[tuple[str, ...]] = []

    def fake_gh(*args: str) -> str:
        calls.append(args)
        return token

    monkeypatch.setattr(fastmcp_triage, "_gh", fake_gh)
    out = fastmcp_triage.publish.fn(
        {"number": 1, "kind": "issue"}, clone, {"result": _result()}, "run"
    )
    assert out.get("blocked")
    assert not [c for c in calls if c[:2] == ("pr", "create")]
    pushed = subprocess.run(
        ["git", "-C", str(tmp_path / "remote.git"), "branch", "--list"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert pushed.strip() == ""


# --- the plan usage gate -----------------------------------------------------


def _snapshot(
    five: float, seven: float, *, five_resets: float = 2e9, seven_resets: float = 2e9
) -> dict:
    return {
        "rate_limits": {
            "five_hour": {"used_percentage": five, "resets_at": five_resets},
            "seven_day": {"used_percentage": seven, "resets_at": seven_resets},
        },
        "saved_at": 1e9,
    }


def test_runs_only_while_both_windows_are_under_the_threshold():
    from flows.fastmcp_triage import usage_verdict

    assert usage_verdict(_snapshot(16, 25), 1e9, 50)[0] is True
    assert usage_verdict(_snapshot(60, 25), 1e9, 50)[0] is False
    assert usage_verdict(_snapshot(16, 50), 1e9, 50)[0] is False


def test_a_window_that_has_reset_counts_as_empty():
    from flows.fastmcp_triage import usage_verdict

    ok, detail = usage_verdict(_snapshot(90, 25, five_resets=1e9 - 1), 1e9, 50)
    assert ok is True
    assert "5h 0%" in detail


def test_unknown_usage_defers():
    from flows.fastmcp_triage import usage_verdict

    assert usage_verdict(None, 1e9, 50) == (False, "no usage snapshot")
    assert usage_verdict({"saved_at": 1e9}, 1e9, 50)[0] is False
