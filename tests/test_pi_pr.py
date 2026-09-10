import pytest
from prefect.testing.utilities import prefect_test_harness

from flows import pi_pr


@pytest.mark.parametrize("dry_run", [False, True])
def test_pi_pr_publishes_as_gardener_and_emits_proposed(monkeypatch, dry_run):
    loaded, published, events = [], {}, []
    artifacts = []
    monkeypatch.setattr(
        pi_pr, "create_table_artifact", lambda **kw: artifacts.append(kw) or "artifact-id"
    )

    def secret_sync(name):
        loaded.append(name)
        return f"<{name}>"

    monkeypatch.setattr(pi_pr, "secret_sync", secret_sync)
    monkeypatch.setattr(pi_pr, "screen_prompt", lambda *a, **k: None)

    class Proc:
        stdout = "abc123\n"

    monkeypatch.setattr(pi_pr.subprocess, "run", lambda *a, **k: Proc())

    def run_pi(*args, **kwargs):
        assert "Gardener (gardener.pds.zat.dev)" in args[0]
        assert args[0].endswith("rename x")
        assert kwargs["provider"] == "aperture"
        assert kwargs["model"] == "openai/gpt-5.6-luna"
        return "done"

    monkeypatch.setattr(pi_pr, "run_pi", run_pi)
    monkeypatch.setattr(
        pi_pr,
        "build_patch",
        lambda cwd, base, title, author, email=None: f"From 0 by {author} <{email}>",
    )

    def create_pull(owner, repo, title, patch, body, handle, password):
        published.update(repo=repo, body=body, handle=handle, patch=patch)
        return {
            "uri": "at://did:plc:g/sh.tangled.repo.pull/1",
            "url": "https://tangled.org/x/pulls",
        }

    monkeypatch.setattr(pi_pr, "create_pull", create_pull)
    monkeypatch.setattr(pi_pr, "emit_event", lambda **kw: events.append(kw))

    with prefect_test_harness():
        out = pi_pr.pi_pr(
            "rename x", "title", "body", repo="tangled-mcp", requested_by="phi", dry_run=dry_run
        )

    assert out["changed"] is True
    if dry_run:
        assert published == {} and events == []
        assert "gardener-password" not in loaded
        assert out["artifact_id"] == "artifact-id"
        assert artifacts[0]["table"][0]["patch"].startswith("From 0")
        assert artifacts[0]["table"][0]["sha256"] == out["patch_sha256"]
        return
    assert published["handle"] == "<gardener-handle>"
    assert "atproto-handle" not in loaded and "atproto-password" not in loaded
    assert "by gardener <gardener@zat.dev>" in published["patch"]
    assert "requested by phi" in published["body"]
    assert "implemented by gardener using the Pi harness" in published["body"]
    assert events[0]["event"] == "autofix.proposed"
    assert events[0]["payload"]["pull"] == "at://did:plc:g/sh.tangled.repo.pull/1"
