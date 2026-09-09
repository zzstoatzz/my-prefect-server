from __future__ import annotations

from unittest.mock import Mock

import pytest

from flows import merge_approved as module
from flows.merge_approved import awaiting_summary, protected_touches


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
    "change", [{"rounds": 2}, {"target_repo_did": "other"}, {"branch": "release"}]
)
def test_same_patch_in_changed_revision_cannot_reuse_review(monkeypatch, change):
    details = {"patch": "unchanged patch", "rounds": 1, "target_repo_did": "repo", "branch": "main"}
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
