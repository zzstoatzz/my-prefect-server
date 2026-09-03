from __future__ import annotations

import subprocess

from mps.tangled import build_patch


def _git_repo(tmp_path) -> tuple[str, str]:
    cwd = str(tmp_path)
    subprocess.run(["git", "init", "-q"], cwd=cwd, check=True)
    (tmp_path / "pin.txt").write_text("v1\n")
    subprocess.run(["git", "add", "-A"], cwd=cwd, check=True)
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init"],
        cwd=cwd,
        check=True,
    )
    base = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    return cwd, base


def test_build_patch_renders_the_change(tmp_path) -> None:
    cwd, base = _git_repo(tmp_path)
    (tmp_path / "pin.txt").write_text("v2\n")
    patch = build_patch(cwd, base, "deps: lib v2", "dep-bump")
    assert "deps: lib v2" in patch
    assert "-v1" in patch and "+v2" in patch
    assert "From:" in patch  # format-patch shape, what create_pull uploads


def test_build_patch_empty_when_nothing_changed(tmp_path) -> None:
    cwd, base = _git_repo(tmp_path)
    assert build_patch(cwd, base, "deps: lib v2", "dep-bump") == ""


def test_default_branch_falls_back_to_main_when_the_knot_says_404(monkeypatch):
    """the appview's knot proxy returned RepoNotFound for the bot repo on
    2026-09-03 and a finished 16KB gardener patch was thrown away."""
    from mps import tangled

    def boom(nsid, **params):
        raise RuntimeError(f"{nsid} failed (404) repository not found on this knot")

    monkeypatch.setattr(tangled, "_bobbin", boom)
    assert tangled._default_branch("at://did:plc:x/sh.tangled.repo/3abc") == "main"


def test_default_branch_uses_the_appview_answer(monkeypatch):
    from mps import tangled

    monkeypatch.setattr(tangled, "_bobbin", lambda nsid, **p: {"name": "trunk"})
    assert tangled._default_branch("at://did:plc:x/sh.tangled.repo/3abc") == "trunk"


def test_repo_record_is_read_from_the_owners_pds(monkeypatch):
    """the appview answered listRepos with a 500 on 2026-09-03; the PDS is
    the authority and was fine."""
    from mps import tangled

    pages = {
        None: {
            "records": [
                {
                    "uri": "at://did:plc:o/sh.tangled.repo/3tid",
                    "value": {"name": "other"},
                }
            ],
            "cursor": "c1",
        },
        "c1": {
            "records": [
                {"uri": "at://did:plc:o/sh.tangled.repo/3bot", "value": {"name": "bot"}}
            ]
        },
    }

    class Resp:
        def __init__(self, cursor):
            self.is_success = True
            self._page = pages[cursor]

        def json(self):
            return self._page

    monkeypatch.setattr(tangled, "resolve_pds", lambda did: "https://pds.example")
    monkeypatch.setattr(
        tangled.httpx,
        "get",
        lambda url, params=None, timeout=None: Resp(params.get("cursor")),
    )
    monkeypatch.setattr(
        tangled,
        "_bobbin",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("appview used")),
    )
    uri, value = tangled._resolve_repo_record("did:plc:o", "bot")
    assert uri.endswith("/3bot") and value["name"] == "bot"
