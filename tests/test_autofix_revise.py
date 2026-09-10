from mps.tangled import comment_subject, comment_text
from prefect.testing.utilities import prefect_test_harness

from flows import autofix_revise, watch_tangled_pulls

PULL = f"{watch_tangled_pulls.PULL_PREFIX}3abc"


def feed_event(rkey="3k1", subject=PULL, op="create", collection=None):
    return {
        "did": "did:plc:xbtmt2zjwlrfegqvch7fboei",
        "time_us": 1_800_000_000_000_000,
        "commit": {
            "operation": op,
            "collection": collection or watch_tangled_pulls.FEED_COMMENT_NSID,
            "rkey": rkey,
            "record": {
                "subject": {"uri": subject, "cid": "bafy"},
                "body": {"text": "please tighten this"},
                "createdAt": "2026-09-01T00:00:00Z",
            },
        },
    }


def test_relevant_comment_matches_gardener_pulls_only():
    assert watch_tangled_pulls.relevant_comment(feed_event())["pull"] == PULL
    other = feed_event(subject="at://did:plc:other/sh.tangled.repo.pull/x")
    assert watch_tangled_pulls.relevant_comment(other) is None
    assert watch_tangled_pulls.relevant_comment(feed_event(op="delete")) is None
    wrong = feed_event(collection="app.bsky.feed.post")
    assert watch_tangled_pulls.relevant_comment(wrong) is None


def test_comment_lexicon_normalization():
    feed = {"subject": {"uri": PULL, "cid": "x"}, "body": {"text": "hi"}}
    legacy = {"pull": PULL, "body": "hi"}
    assert comment_subject(feed) == comment_subject(legacy) == PULL
    assert comment_text(feed) == comment_text(legacy) == "hi"


async def test_watch_dedupes_handled_comments(monkeypatch):
    started = []

    async def fake_drain(cursor):
        return [watch_tangled_pulls.relevant_comment(feed_event())], 123

    async def fake_run_deployment(name, parameters, timeout, idempotency_key):
        assert idempotency_key == f"autofix-revise:{parameters['comment_uri']}"
        started.append(parameters)

        class R:
            id = "run"

        return R()

    monkeypatch.setattr(watch_tangled_pulls, "drain", fake_drain)
    monkeypatch.setattr(watch_tangled_pulls, "reconcile", list)
    monkeypatch.setattr(watch_tangled_pulls, "arun_deployment", fake_run_deployment)
    with prefect_test_harness():
        assert await watch_tangled_pulls.watch_tangled_pulls() == 1
        assert await watch_tangled_pulls.watch_tangled_pulls() == 0
    assert len(started) == 1


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


async def test_watch_survives_stream_failure(monkeypatch):
    async def broken(cursor):
        raise OSError("stream down")

    comment = watch_tangled_pulls.relevant_comment(feed_event(rkey="3k9"))
    monkeypatch.setattr(watch_tangled_pulls, "drain", broken)
    monkeypatch.setattr(watch_tangled_pulls, "reconcile", lambda: [comment])

    started = []

    async def fake_run_deployment(name, parameters, timeout, idempotency_key):
        assert idempotency_key == f"autofix-revise:{parameters['comment_uri']}"
        started.append(parameters)

        class R:
            id = "run"

        return R()

    monkeypatch.setattr(watch_tangled_pulls, "arun_deployment", fake_run_deployment)
    with prefect_test_harness():
        assert await watch_tangled_pulls.watch_tangled_pulls() == 1
    assert started[0]["pull"] == PULL


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


def test_only_phi_change_requests_start_revisions():
    event = feed_event()
    event["did"] = autofix_revise.PHI_DID
    for verdict in ("approve", "escalate"):
        event["commit"]["record"]["body"]["text"] = f"VERDICT: {verdict}"
        assert watch_tangled_pulls.relevant_comment(event) is None
    event["commit"]["record"]["body"]["text"] = "VERDICT: request-changes"
    assert watch_tangled_pulls.relevant_comment(event)["pull"] == PULL
    event["did"] = "did:plc:stranger"
    assert watch_tangled_pulls.relevant_comment(event) is None


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
