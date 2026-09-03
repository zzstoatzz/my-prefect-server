from __future__ import annotations

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
