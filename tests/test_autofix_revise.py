from mps.pull_comments import PULL_PREFIX
from prefect.testing.utilities import prefect_test_harness

from flows import autofix_revise

PULL = f"{PULL_PREFIX}3abc"


def test_revise_skips_foreign_pull():
    with prefect_test_harness():
        state = autofix_revise.autofix_revise(
            "at://did:plc:someoneelse/sh.tangled.repo.pull/x", return_state=True
        )
    assert state.name == "Skipped"


def test_revise_caps_rounds(monkeypatch):
    monkeypatch.setattr(
        autofix_revise,
        "get_record",
        lambda uri: {"value": {"rounds": [{}] * autofix_revise.MAX_ROUNDS}},
    )
    with prefect_test_harness():
        state = autofix_revise.autofix_revise(PULL, return_state=True)
    assert state.name == "Capped"


def test_revise_skips_without_operator_comments(monkeypatch):
    monkeypatch.setattr(autofix_revise, "get_record", lambda uri: {"value": {"rounds": []}})
    monkeypatch.setattr(autofix_revise, "list_pull_comments", lambda did, pull: [])
    with prefect_test_harness():
        state = autofix_revise.autofix_revise(PULL, return_state=True)
    assert state.name == "Skipped"


def test_new_round_patch_is_self_contained(tmp_path):
    import subprocess

    env = {
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@t",
        "PATH": "/usr/bin:/bin:/opt/homebrew/bin",
    }

    def git(*args, cwd):
        return subprocess.run(
            ["git", *args], cwd=cwd, env=env, check=True, capture_output=True, text=True
        ).stdout

    repo = tmp_path / "r"
    repo.mkdir()
    git("init", "-q", "-b", "main", ".", cwd=repo)
    (repo / "a").write_text("base\n")
    git("add", "a", cwd=repo)
    git("commit", "-q", "-m", "base", cwd=repo)
    base = git("rev-parse", "HEAD", cwd=repo).strip()

    (repo / "a").write_text("round one\n")
    git("add", "a", cwd=repo)
    git("commit", "-q", "-m", "round one", cwd=repo)
    round1 = git("format-patch", f"{base}..HEAD", "--stdout", cwd=repo)
    git("reset", "-q", "--hard", base, cwd=repo)

    from mps.tangled import build_patch

    assert autofix_revise.apply_patch(str(repo), round1)
    # A reply-only run must not republish the already-applied round.
    assert build_patch(str(repo), base, "reply only", "gardener") == ""
    (repo / "a").write_text("round one\nrevised\n")
    new_round = build_patch(str(repo), base, "revision", "gardener")

    # the new round applies to a clean checkout of main on its own
    clean = tmp_path / "clean"
    git("clone", "-q", str(repo), str(clean), cwd=tmp_path)
    git("checkout", "-q", base, cwd=clean)
    subprocess.run(
        ["git", "am"],
        cwd=clean,
        env=env,
        input=new_round,
        text=True,
        check=True,
        capture_output=True,
    )
    assert (clean / "a").read_text() == "round one\nrevised\n"


def test_revision_downloads_validated_snapshot_without_refetch(monkeypatch):
    import gzip

    snapshot = {"rounds": [{"patchBlob": {"ref": {"$link": "reviewed-patch"}}}]}

    def unexpected_refetch(*args):
        raise AssertionError("Must not select a newer pull while preparing this revision")

    def get(url, *, params, timeout):
        assert params["cid"] == "reviewed-patch"

        class Response:
            content = gzip.compress(b"reviewed patch contents")

            def raise_for_status(self):
                pass

        return Response()

    monkeypatch.setattr(autofix_revise, "get_record", unexpected_refetch)
    monkeypatch.setattr(autofix_revise, "resolve_pds", lambda did: "https://pds.example")
    monkeypatch.setattr(autofix_revise.httpx, "get", get)
    assert autofix_revise.latest_round_patch(snapshot) == "reviewed patch contents"


def test_phi_feedback_for_other_record_is_stale_even_in_same_round(monkeypatch):
    comment_uri = f"at://{autofix_revise.PHI_DID}/sh.tangled.feed.comment/review"
    pull = {"cid": "current-cid", "value": {"rounds": [{}]}}
    comment = {
        "value": {
            "subject": {"uri": PULL, "cid": "older-cid"},
            "pullRoundIdx": 0,
            "body": {"text": "VERDICT: request-changes"},
        }
    }
    monkeypatch.setattr(autofix_revise, "get_record", lambda uri: pull if uri == PULL else comment)
    monkeypatch.setattr(autofix_revise, "list_pull_comments", lambda *args: [])

    def unexpected_secret(*args):
        raise AssertionError("stale review must stop before credentials or execution")

    monkeypatch.setattr(autofix_revise, "secret_sync", unexpected_secret)
    result = autofix_revise.autofix_revise.fn(PULL, comment_uri)
    assert result.name == "Stale"
