from prefect.testing.utilities import prefect_test_harness

from flows import pi_pr


def test_pi_pr_publishes_as_gardener_and_emits_proposed(monkeypatch):
    loaded, published, events = [], {}, []

    class FakeSecret:
        def __init__(self, name):
            self.name = name

        def get(self):
            return f"<{self.name}>"

    def load(name):
        loaded.append(name)
        return FakeSecret(name)

    monkeypatch.setattr(pi_pr.Secret, "load", staticmethod(load))
    monkeypatch.setattr(pi_pr, "screen_prompt", lambda *a, **k: None)

    class Proc:
        stdout = "abc123\n"

    monkeypatch.setattr(pi_pr.subprocess, "run", lambda *a, **k: Proc())
    monkeypatch.setattr(pi_pr, "run_pi", lambda *a, **k: "done")
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
        out = pi_pr.pi_pr("rename x", "title", "body", repo="tangled-mcp", requested_by="phi")

    assert out["changed"] is True
    assert published["handle"] == "<gardener-handle>"
    assert "atproto-handle" not in loaded and "atproto-password" not in loaded
    assert "by gardener <gardener@zat.dev>" in published["patch"]
    assert "requested by phi" in published["body"]
    assert events[0]["event"] == "autofix.proposed"
    assert events[0]["payload"]["pull"] == "at://did:plc:g/sh.tangled.repo.pull/1"
