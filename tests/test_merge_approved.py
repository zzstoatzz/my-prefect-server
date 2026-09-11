from __future__ import annotations

from unittest.mock import AsyncMock, Mock

import pytest

from flows import merge_approved as module
from flows.merge_approved import awaiting_summary, protected_touches


def test_approval_card_keeps_long_details_behind_a_link():
    card = module.approval_card(
        {"title": "Retain proposed patches", "rounds": 2, "patch": "large diff" * 10000},
        "my-prefect-server",
        "https://example.test/details",
        "https://example.test/approve",
    )
    assert "large diff" not in card
    assert len(card) < 500
    assert "my-prefect-server/main" in card
    assert "Approving merges" in card
    assert "https://example.test/details" in card
    assert "https://example.test/approve" in card


def test_protected_touches_match_files_and_prefix_dirs() -> None:
    paths = [
        "src/bot/core/policy.py",
        "src/bot/core/thread_frame.py",
        "deploy/fly/thing.toml",
        "deployment_notes.md",
        "fly.toml",
    ]
    assert protected_touches("bot", paths) == [
        "src/bot/core/policy.py",
        "deploy/fly/thing.toml",
        "fly.toml",
    ]


def test_protected_touches_empty_for_unknown_repo() -> None:
    assert protected_touches("find-bufo", ["deploy/x"]) == []


def test_awaiting_summary_flags_protected_and_links_the_run() -> None:
    text = awaiting_summary(
        "policy: thread-fit",
        "bot",
        ["a.py", "b.py"],
        ["a.py"],
        "https://x/runs/flow-run/1",
    )
    assert text.splitlines() == [
        "policy: thread-fit (bot, 2 files, phi approved, tests green)",
        "protected: a.py",
        "resume to merge: https://x/runs/flow-run/1",
    ]


def test_awaiting_summary_omits_protected_line_when_clean() -> None:
    text = awaiting_summary("t", "bot", ["a.py"], [], "u")
    assert "protected" not in text


@pytest.mark.parametrize(
    "change",
    [{"cid": "changed"}, {"rounds": 2}, {"target_repo_did": "other"}, {"branch": "release"}],
)
def test_same_patch_in_changed_revision_cannot_reuse_review(monkeypatch, change):
    details = {
        "cid": "reviewed",
        "patch": "unchanged patch",
        "rounds": 1,
        "target_repo_did": "repo",
        "branch": "main",
    }
    reads = iter([details, {**details, **change}])
    monkeypatch.setattr(module, "pull_patch", lambda _: next(reads))
    monkeypatch.setattr(module, "wait_for_verdict", lambda *args: {"verdict": "approve"})
    secret = Mock(side_effect=AssertionError("must not access merge credentials"))
    monkeypatch.setattr(module, "secret_sync", secret)
    state = module.merge_approved.fn(module.PULL_PREFIX + "test")
    assert state.name == "Stale"
    secret.assert_not_called()
    assert module.approval_key(details) != module.approval_key({**details, **change})


def test_new_round_during_validation_cannot_use_existing_human_approval(monkeypatch):
    details = {
        "cid": "reviewed",
        "patch": "same patch",
        "rounds": 1,
        "target_repo_did": "repo",
        "branch": "main",
        "title": "test",
    }
    reads = iter([details, details, {**details, "rounds": 2}])
    monkeypatch.setattr(module, "pull_patch", lambda _: next(reads))
    monkeypatch.setattr(module, "wait_for_verdict", lambda *args: {"verdict": "approve"})
    monkeypatch.setattr(module, "repo_name_for_did", lambda *args: "bot")
    monkeypatch.setattr(module, "touched_paths", lambda _: [])
    monkeypatch.setattr(module, "_already_asked", lambda key: key == module.approval_key(details))
    monkeypatch.setattr(module, "secret_sync", lambda _: "fake")
    monkeypatch.setattr(module, "_ssh_env", lambda *args: {})
    monkeypatch.setattr(module, "knot_head", lambda *args: "base")
    monkeypatch.setattr(module, "clone_and_apply", lambda *args: "base")
    monkeypatch.setattr(module, "run_tests", lambda *args: (True, "passed"))
    push = Mock(side_effect=AssertionError("must not push a stale approval"))
    monkeypatch.setattr(module, "push_merge", push)
    state = module.merge_approved.fn(module.PULL_PREFIX + "test")
    assert state.name == "Stale"
    push.assert_not_called()


def test_validation_runs_on_the_sprite_deployment(monkeypatch):
    result = Mock()
    result.state.is_completed.return_value = True
    monkeypatch.setattr(
        module,
        "read_test_result",
        lambda _: {
            "base": "base-sha",
            "passed": True,
            "tail": "ok",
            "patch_sha256": module.hashlib.sha256(b"patch").hexdigest(),
        },
    )
    dispatch = AsyncMock(return_value=result)
    monkeypatch.setattr("prefect.deployments.arun_deployment", dispatch)
    monkeypatch.setattr(
        module.subprocess, "run", Mock(side_effect=AssertionError("no local tests"))
    )
    assert module.run_tests.fn("bot", "base-sha", "patch") == (True, "ok")
    assert dispatch.call_args.args == ("test-pull-patch/test-pull-patch",)
    assert dispatch.call_args.kwargs["parameters"] == {
        "repo": "bot",
        "base": "base-sha",
        "patch": "patch",
    }


def test_incomplete_sprite_tests_cannot_approve_merge(monkeypatch):
    result = Mock()
    result.state.is_completed.return_value = False
    monkeypatch.setattr("prefect.deployments.arun_deployment", AsyncMock(return_value=result))
    assert module.run_tests.fn("bot", "base", "patch")[0] is False


@pytest.mark.parametrize("field", ["base", "patch_sha256"])
def test_test_artifact_must_match_requested_patch(monkeypatch, field):
    run = Mock()
    run.state.is_completed.return_value = True
    outcome = {
        "base": "base",
        "patch_sha256": module.hashlib.sha256(b"patch").hexdigest(),
        "passed": True,
        "tail": "ok",
    }
    outcome[field] = "different"
    monkeypatch.setattr("prefect.deployments.arun_deployment", AsyncMock(return_value=run))
    monkeypatch.setattr(module, "read_test_result", lambda _: outcome)
    with pytest.raises(RuntimeError, match="does not match"):
        module.run_tests.fn("bot", "base", "patch")
