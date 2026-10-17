import os
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import httpx
import pytest
from prefect.exceptions import ObjectNotFound
from prefect.testing.utilities import prefect_test_harness

from flows import autofix


class FakeState:
    def __init__(self, name, message):
        self.name, self.message = name, message
        self.timestamp = datetime(2026, 9, 10, tzinfo=UTC)


class FakeDeployment:
    def __init__(self, name):
        self.name, self.entrypoint = name, "flows/x.py:x"


class FakeRun:
    def __init__(self, name):
        self.id, self.name = uuid4(), name
        self.state = FakeState("Failed", "boom")
        self.parameters = {}
        self.start_time = self.end_time = None
        self.created = datetime(2026, 9, 9, tzinfo=UTC)


@pytest.fixture(autouse=True)
def no_skill_clones(monkeypatch):
    monkeypatch.setattr(autofix, "fetch_skills", lambda workdir: [])
    monkeypatch.setattr(autofix, "prior_attempt", AsyncMock(return_value=None))


def ctx(dep_name):
    return {
        "run": FakeRun("r"),
        "deployment": FakeDeployment(dep_name),
        "logs": [],
        "failed_tasks": [],
    }


async def test_gather_preserves_evidence_after_deployment_deletion(monkeypatch):
    run = FakeRun("startup-crash")
    run.deployment_id = uuid4()
    logs = [SimpleNamespace(message="older", level=20), SimpleNamespace(message="newer", level=40)]
    client = AsyncMock()
    client.read_flow_run.return_value = run
    request = httpx.Request("GET", "https://prefect.example/api/deployments/deleted")
    client.read_deployment.side_effect = ObjectNotFound(
        httpx.HTTPStatusError("deleted", request=request, response=httpx.Response(404))
    )
    client.read_logs.return_value = list(reversed(logs))
    client.read_task_runs.return_value = []
    client.__aenter__.return_value = client
    monkeypatch.setattr(autofix, "get_client", lambda: client)

    evidence = await autofix.gather(run.id)

    assert evidence["run"] is run
    assert evidence["deployment"] is None
    assert evidence["logs"] == logs
    assert "deleted or unavailable" in autofix.render(evidence)
    client.read_deployment.side_effect = RuntimeError("API unavailable")
    with pytest.raises(RuntimeError, match="API unavailable"):
        await autofix.gather(run.id)


@pytest.mark.parametrize("clock", ["start", "state", "created"])
def test_diagnosis_uses_available_run_timestamp(monkeypatch, clock):
    evidence = ctx("strata")
    run = evidence["run"]
    if clock == "start":
        run.start_time = datetime(2026, 9, 9, 23, tzinfo=UTC)
        expected = run.start_time
    elif clock == "state":
        expected = run.state.timestamp
    else:
        run.state = None
        expected = run.created
    monkeypatch.setattr(autofix, "gather", AsyncMock(return_value=evidence))
    observed = []

    def checkout(cwd, when):
        observed.append(when)
        return "abc"

    monkeypatch.setattr(autofix, "checkout_as_of", checkout)
    monkeypatch.setattr(autofix, "secret_sync", lambda _: "fake")
    monkeypatch.setattr(autofix, "screen_prompt", lambda *args: None)
    monkeypatch.setattr(autofix, "run_pi", lambda *args, **kwargs: "SUMMARY: diagnosis")
    monkeypatch.setattr(autofix, "emit_event", lambda **kwargs: None)
    monkeypatch.setattr(autofix, "run_context", SimpleNamespace(id=None))

    result = autofix.autofix.fn(run.id)

    assert result.name == "Diagnosed"
    assert observed == [expected]


def test_gather_error_never_fails_the_run(monkeypatch):
    async def broken(_):
        raise RuntimeError("api down")

    monkeypatch.setattr(autofix, "gather", broken)
    with prefect_test_harness():
        state = autofix.autofix(uuid4(), return_state=True)
    assert state.is_completed()
    assert state.name == "Degraded"
    assert "api down" in state.message


def test_skips_own_failures(monkeypatch):
    async def fake(_):
        return ctx("autofix")

    monkeypatch.setattr(autofix, "gather", fake)
    with prefect_test_harness():
        state = autofix.autofix(uuid4(), return_state=True)
    assert state.name == "Skipped"


def test_dry_run_renders_without_pi(monkeypatch):
    async def fake(_):
        return ctx("strata")

    monkeypatch.setattr(autofix, "gather", fake)
    monkeypatch.setattr(autofix, "run_pi", lambda *a, **k: (_ for _ in ()).throw(AssertionError))
    with prefect_test_harness():
        state = autofix.autofix(uuid4(), dry_run=True, return_state=True)
    assert state.name == "DryRun"


def test_split_summary():
    s, rest = autofix.split_summary("SUMMARY: retries too short; skip the segment\nbody\nmore")
    assert s == "retries too short; skip the segment"
    assert rest == "body\nmore"
    s, rest = autofix.split_summary("no header\nbody")
    assert s == "no header" and rest == "no header\nbody"
    assert len(autofix.split_summary("SUMMARY: " + "x" * 999)[0]) == autofix.SUMMARY_LIMIT


def test_checkout_as_of_picks_commit_before_run(tmp_path, monkeypatch):
    import subprocess
    from datetime import UTC, datetime

    src = tmp_path / "src"
    subprocess.run(["git", "init", "-q", "-b", "main", src], check=True)
    env = {
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@t",
        "PATH": os.environ["PATH"],
    }
    shas = []
    for i, date in enumerate(["2026-01-01T00:00:00Z", "2026-01-03T00:00:00Z"]):
        (src / "f").write_text(str(i))
        subprocess.run(["git", "add", "f"], cwd=src, check=True)
        subprocess.run(
            ["git", "commit", "-q", "-m", str(i)],
            cwd=src,
            check=True,
            env={**env, "GIT_AUTHOR_DATE": date, "GIT_COMMITTER_DATE": date},
        )
        shas.append(
            subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=src,
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()
        )
    monkeypatch.setattr(autofix, "REPO_URL", str(src))
    dst = tmp_path / "dst"
    got = autofix.checkout_as_of(str(dst), datetime(2026, 1, 2, tzinfo=UTC))
    assert got == shas[0]
    assert (dst / "f").read_text() == "0"


def test_trailer_parses_last_lines():
    out = "did stuff\nTITLE: strata: lengthen retry\nNOTE: bumped delays"
    assert autofix.trailer(out, "TITLE", siblings=("NOTE",)) == "strata: lengthen retry"
    assert autofix.trailer(out, "NOTE") == "bumped delays"
    assert autofix.trailer(out, "NO-CHANGE") == ""


def test_trailers_keep_multiline_note():
    out = "TITLE: strata: skip stuck segments\nNOTE: the punchline.\n\nmore detail\n- a bullet"
    parsed = autofix.trailers(out, ("TITLE", "NOTE"))
    assert parsed["TITLE"] == "strata: skip stuck segments"
    assert parsed["NOTE"] == "the punchline.\n\nmore detail\n- a bullet"


def test_propose_off_by_default(monkeypatch):
    async def fake(_):
        return ctx("strata")

    monkeypatch.setattr(autofix, "gather", fake)
    monkeypatch.setattr(autofix, "checkout_as_of", lambda cwd, when: "abc")
    monkeypatch.setattr(autofix, "run_pi", lambda *a, **k: "SUMMARY: x\nbody")
    monkeypatch.setattr(autofix, "screen_prompt", lambda *a, **k: None)
    monkeypatch.setattr(autofix, "secret_sync", lambda name: "k")
    monkeypatch.setattr(
        autofix, "propose_fix", lambda *a, **k: (_ for _ in ()).throw(AssertionError)
    )
    with prefect_test_harness():
        state = autofix.autofix(uuid4(), return_state=True)
    assert state.name == "Diagnosed"


def test_no_change_yields_no_pr(monkeypatch):
    monkeypatch.setattr(autofix, "screen_prompt", lambda *a, **k: None)
    monkeypatch.setattr(
        autofix,
        "run_pi",
        lambda *a, **k: "looked around\nNO-CHANGE: already fixed on main",
    )

    def no_clone(cmd, **kw):
        class R:
            stdout = "sha\n"

        return R()

    monkeypatch.setattr(autofix.subprocess, "run", no_clone)
    result = autofix.propose_fix("diag", "brief", "key", "strata")
    assert result == {"reason": "already fixed on main"}


def test_propose_for_allowlist_turns_proposing_on(monkeypatch):
    proposed = []

    async def fake(_):
        return ctx("strata-hourly")

    monkeypatch.setattr(autofix, "gather", fake)
    monkeypatch.setattr(autofix, "checkout_as_of", lambda cwd, when: "abc")
    monkeypatch.setattr(autofix, "run_pi", lambda *a, **k: "SUMMARY: x\nbody")
    monkeypatch.setattr(autofix, "screen_prompt", lambda *a, **k: None)
    monkeypatch.setattr(autofix, "secret_sync", lambda name: "k")

    def fake_propose(diagnosis, brief, key, dep_name, since=""):
        proposed.append(dep_name)
        return {
            "title": "t",
            "uri": "at://did:plc:g/sh.tangled.repo.pull/1",
            "url": "https://tangled.org/x/pulls",
        }

    monkeypatch.setattr(autofix, "propose_fix", fake_propose)
    with prefect_test_harness():
        other = autofix.autofix(uuid4(), propose_for=["strata-hourly"], return_state=True)
    assert other.name == "Proposed"
    assert proposed == ["strata-hourly"]

    async def fake_other(_):
        return ctx("ingest")

    monkeypatch.setattr(autofix, "gather", fake_other)
    with prefect_test_harness():
        state = autofix.autofix(uuid4(), propose_for=["strata-hourly"], return_state=True)
    assert state.name == "Diagnosed"
    assert proposed == ["strata-hourly"]


def test_match_proposal_points_at_open_pull_for_same_sha():
    def ev(event, occurred, **payload):
        return SimpleNamespace(event=event, occurred=occurred, payload=payload)

    events = [
        ev("autofix.proposed", 1, deployment="strata-hourly", sha="abc", pull="p1", pr_url="u1"),
        ev("autofix.proposed", 2, deployment="strata-hourly", sha="abc", pull="p2", pr_url="u2"),
        ev("autofix.proposed", 3, deployment="ingest", sha="abc", pull="p3", pr_url="u3"),
    ]
    assert autofix.match_proposal(events, "strata-hourly", "abc") == "u2"
    assert autofix.match_proposal(events, "strata-hourly", "def") is None
    assert autofix.match_proposal(events, "ingest", "abc") == "u3"
    events.append(ev("merge.merged", 4, pull="p2"))
    assert autofix.match_proposal(events, "strata-hourly", "abc") == "u1"
    assert autofix.match_proposal([], "strata-hourly", "abc") is None


def test_second_failure_on_same_sha_does_not_open_another_pull(monkeypatch):
    async def fake(_):
        return ctx("strata-hourly")

    async def already_open(dep_name, sha):
        assert (dep_name, sha) == ("strata-hourly", "abc")
        return "https://tangled.org/x/pulls/9"

    monkeypatch.setattr(autofix, "gather", fake)
    monkeypatch.setattr(autofix, "checkout_as_of", lambda cwd, when: "abc")
    monkeypatch.setattr(autofix, "run_pi", lambda *a, **k: "SUMMARY: x\nbody")
    monkeypatch.setattr(autofix, "screen_prompt", lambda *a, **k: None)
    monkeypatch.setattr(autofix, "secret_sync", lambda name: "k")
    monkeypatch.setattr(autofix, "prior_proposal", already_open)
    monkeypatch.setattr(
        autofix, "propose_fix", lambda *a, **k: (_ for _ in ()).throw(AssertionError)
    )
    with prefect_test_harness():
        state = autofix.autofix(uuid4(), propose_for=["strata-hourly"], return_state=True)
    assert state.name == "Diagnosed"
    assert "https://tangled.org/x/pulls/9" in state.message


def test_recent_changes_lists_commits_main_gained_after_revision(tmp_path):
    import subprocess

    repo = tmp_path / "repo"
    repo.mkdir()
    env = {
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@x",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@x",
        "PATH": os.environ["PATH"],
    }

    def git(*args):
        return subprocess.run(
            ["git", *args], cwd=repo, check=True, capture_output=True, text=True, env=env
        ).stdout.strip()

    git("init", "-q", "-b", "main")
    (repo / "a").write_text("1")
    git("add", "a")
    git("commit", "-q", "-m", "first")
    first = git("rev-parse", "HEAD")
    (repo / "a").write_text("2")
    git("commit", "-q", "-am", "strata: fix the swap")
    git("checkout", "-q", first)
    out = autofix.recent_changes(str(repo), first)
    assert "strata: fix the swap" in out and "first" not in out
    assert autofix.recent_changes(str(repo), git("rev-parse", "main")) == "none"


def test_describe_proposals_shows_each_pull_with_phi_verdict():
    def ev(occurred, **payload):
        return SimpleNamespace(event="autofix.proposed", occurred=occurred, payload=payload)

    events = [
        ev(1, deployment="strata-hourly", pull="p1", pr_url="u1", title="cast it", sha="abc1234"),
        ev(2, deployment="strata-hourly", pull="p1", pr_url="u1", title="cast it", sha="abc1234"),
        ev(3, deployment="ingest", pull="p9", pr_url="u9", title="other"),
    ]
    verdicts = {"p1": (2, "request-changes", "VERDICT: request-changes\n\nhides the corruption")}
    text = autofix.describe_proposals(events, "strata-hourly", lambda uri: verdicts[uri])
    assert text.count("cast it") == 1
    assert (
        "round 2, sha abc1234" in text
        and "phi: request-changes: VERDICT: request-changes hides the corruption" in text
    )
    assert "other" not in text
    assert autofix.describe_proposals(events, "brief", lambda uri: verdicts[uri]) == "  none"

    def broken(uri):
        raise RuntimeError("pds down")

    assert "verdict unavailable: pds down" in autofix.describe_proposals(
        events, "strata-hourly", broken
    )


def test_fixed_on_main_diagnosis_skips_the_fix_round(monkeypatch):
    async def fake(_):
        return ctx("strata-hourly")

    monkeypatch.setattr(autofix, "gather", fake)
    monkeypatch.setattr(autofix, "checkout_as_of", lambda cwd, when: "abc")
    monkeypatch.setattr(
        autofix, "recent_changes", lambda cwd, sha: "9925475 2026-10-12 strata: name insert columns"
    )
    monkeypatch.setattr(
        autofix,
        "prior_work",
        lambda dep: "prior gardener proposals for strata-hourly (last 7 days):\n  none",
    )
    monkeypatch.setattr(
        autofix, "run_pi", lambda *a, **k: "SUMMARY: landed in 992475c\nFIXED-ON-MAIN: 992475c"
    )
    monkeypatch.setattr(autofix, "screen_prompt", lambda *a, **k: None)
    monkeypatch.setattr(autofix, "secret_sync", lambda name: "k")
    monkeypatch.setattr(
        autofix, "propose_fix", lambda *a, **k: (_ for _ in ()).throw(AssertionError)
    )
    with prefect_test_harness():
        state = autofix.autofix(uuid4(), propose_for=["strata-hourly"], return_state=True)
    assert state.name == "Diagnosed"
    assert "fixed on main: 992475c" in state.message


def test_diagnosis_round_gets_the_context_file_and_the_skill(monkeypatch, tmp_path):
    seen = {}

    async def fake(_):
        return ctx("strata-hourly")

    def fake_pi(prompt, *, cwd, skills=None, **kw):
        seen["context"] = Path(cwd, autofix.CONTEXT_FILE).read_text()
        seen["skills"] = skills
        return "SUMMARY: x\nbody"

    monkeypatch.setattr(autofix, "gather", fake)
    monkeypatch.setattr(
        autofix, "fetch_skills", lambda workdir: [f"{workdir}/skill-gardener-history"]
    )
    monkeypatch.setattr(autofix, "checkout_as_of", lambda cwd, when: "abc")
    monkeypatch.setattr(
        autofix, "since_section", lambda cwd, sha, dep: f"commits since {sha}: none\nprior: none"
    )
    monkeypatch.setattr(autofix, "run_pi", fake_pi)
    monkeypatch.setattr(autofix, "screen_prompt", lambda *a, **k: None)
    monkeypatch.setattr(autofix, "secret_sync", lambda name: "k")
    with prefect_test_harness():
        state = autofix.autofix(uuid4(), return_state=True)
    assert state.name == "Diagnosed"
    assert "commits since abc: none" in seen["context"]
    assert seen["skills"] and seen["skills"][0].endswith("skill-gardener-history")


def test_autofix_events_name_the_failed_run_without_claiming_to_be_it() -> None:
    run_id = UUID("11111111-1111-4111-8111-111111111111")
    assert autofix.about_run(run_id, "strata-hourly-9") == [
        {
            "prefect.resource.id": f"prefect.flow-run.{run_id}",
            "prefect.resource.name": "strata-hourly-9",
            "prefect.resource.role": "about",
        }
    ]


def test_incident_identity_ignores_request_ids_but_preserves_cause_and_revision():
    evidence = ctx("classify-emails")
    evidence["run"].deployment_id = uuid4()
    evidence["deployment"].job_variables = {"env": {"MPS_PIN": "@abc"}}
    evidence["run"].state.message = "TypeSafeAPIError: 402 no credits (request_id=req_one)"
    first = autofix.incident_key(evidence)
    evidence["run"].id = uuid4()
    evidence["run"].state.message = "TypeSafeAPIError: 402 no credits (request_id=req_two)"
    assert autofix.incident_key(evidence) == first
    evidence["deployment"].job_variables["env"]["MPS_PIN"] = "@def"
    assert autofix.incident_key(evidence) != first
    evidence["deployment"].job_variables["env"]["MPS_PIN"] = "@abc"
    evidence["run"].state.message = "TypeSafeAPIError: 500 unavailable"
    assert autofix.incident_key(evidence) != first
