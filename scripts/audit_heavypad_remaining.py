"""Record live ownership and bounded qualification for the remaining migration."""

import argparse
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import httpx
import yaml
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parent.parent
QUALIFICATIONS = {
    "pub-search-snapshot": (
        "532d5c9b-d743-4bdb-932e-d4c20bbf6d92",
        "production publication",
        "Completed",
    ),
    "leaflet-atlas": (
        "792691b2-7a1f-4346-8ec9-957cf7ac9e31",
        "production publication",
        "Completed",
    ),
    "investigate": (
        "06967b7d-9a90-4e51-9d97-d41bb5fa4a4a",
        "real isolated agent execution",
        "Completed",
    ),
    "pi-agent": ("537c231f-0803-4789-ba4a-6161237e4afd", "real local agent execution", "Completed"),
    "autofix": ("d47393d5-1e0e-41ff-9d1b-163b095b9b9b", "read-only diagnostic gathering", "DryRun"),
    "phi-pull-review": (
        "72410975-77e5-47d3-a9be-b18473a033e0",
        "authenticated slot validation; no review request",
        "Validated",
    ),
    "autofix-revise": (
        "1a2c269c-d64d-41cf-9914-84e1e2fa1309",
        "untrusted-input refusal; no revision",
        "Skipped",
    ),
    "merge-approved": (
        "a0a5c014-9309-4445-b609-01de88a89f42",
        "patched-wheel refusal; no approval/merge",
        "Skipped",
    ),
    "watch-tangled-pulls": (
        "b220b08b-beb7-47c2-bf32-e11e7028cc6c",
        "real reconciliation with transferred checkpoint",
        "Completed",
    ),
    "typeahead-identity-hourly": (
        "ad6f61b7-af7c-4aba-92ec-775c8e41bdde",
        "three-record real identity update",
        "Completed",
    ),
    "typeahead-enrich-backfill": (
        "a52d9386-3ece-4ca4-9138-aaf3bb27fe9b",
        "three-record real profile update",
        "Completed",
    ),
}
SCOPE = set(QUALIFICATIONS) | {"test-pull-patch", "typeahead-index", "typeahead-plc-identity"}
RULES = {
    "flow-run failure -> autofix": "autofix",
    "autofix proposed -> merge-approved": "merge-approved",
    "autofix proposed -> phi review": "phi-pull-review",
    "autofix revise requested -> autofix-revise": "autofix-revise",
}


def request(api, method, path, **kwargs):
    response = api.request(method, path, **kwargs)
    response.raise_for_status()
    return response.json() if response.content else None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--cloud-qualification",
        help="Completed HeavyPad cloud qualification UUID after explicit release handoff",
    )
    parser.add_argument("--require-complete", action="store_true")
    args = parser.parse_args()
    original = dotenv_values(ROOT / ".env")
    credentials = json.loads(
        subprocess.check_output(
            [
                "sops",
                "decrypt",
                "--output-type",
                "json",
                "--extract",
                '["heavypad_prefect"]',
                str(Path.home() / "tangled.org/zzstoatzz.io/secrets/prod.yaml"),
            ],
            text=True,
        )
    )
    declared = {
        d["name"]: d
        for d in yaml.safe_load((ROOT / "prefect.heavypad.yaml").read_text())["deployments"]
        if d["name"] in SCOPE
    }
    assert set(declared) == SCOPE
    with (
        httpx.Client(
            base_url=f"https://{original['DOMAIN']}/api/",
            auth=tuple(original["AUTH_STRING"].split(":", 1)),
            timeout=30,
        ) as old,
        httpx.Client(
            base_url="https://heavypad.tailb660b6.ts.net:10443/api/",
            auth=tuple(credentials["api_auth_string"].split(":", 1)),
            timeout=30,
        ) as new,
    ):
        old_deployments = {
            d["name"]: d for d in request(old, "POST", "deployments/filter", json={"limit": 500})
        }
        new_deployments = {
            d["name"]: d for d in request(new, "POST", "deployments/filter", json={"limit": 500})
        }
        old_ids = {old_deployments[name]["id"] for name in SCOPE}
        active = request(
            old,
            "POST",
            "flow_runs/filter",
            json={
                "flow_runs": {
                    "state": {
                        "type": {
                            "any_": ["SCHEDULED", "PENDING", "RUNNING", "PAUSED", "CANCELLING"]
                        }
                    }
                },
                "limit": 500,
            },
        )
        assert len(active) < 500
        old_active = [
            {k: r[k] for k in ("id", "deployment_id", "state_type", "state_name")}
            for r in active
            if r["deployment_id"] in old_ids
        ]
        pending = []
        records = []
        for name in sorted(SCOPE):
            source, target = old_deployments[name], new_deployments[name]
            assert not source["schedules"], f"Original clocks remain: {name}"
            assert target["work_pool_name"] == declared[name]["work_pool"]["name"], name

            def clock(schedule, active):
                return {
                    "schedule": {
                        "cron": schedule["cron"],
                        "timezone": schedule.get("timezone") or "UTC",
                        "day_or": schedule.get("day_or", True),
                    },
                    "active": active,
                }

            expected_clocks = [
                clock(s, s.get("active", True)) for s in declared[name].get("schedules", [])
            ]
            actual_clocks = [clock(s["schedule"], s["active"]) for s in target["schedules"]]
            assert actual_clocks == expected_clocks, f"Clock/source drift: {name}"
            qualification = None
            if name in QUALIFICATIONS or (name == "typeahead-index" and args.cloud_qualification):
                run_id, mode, state = QUALIFICATIONS.get(
                    name,
                    (
                        args.cloud_qualification,
                        "cloud worker qualification after native release handoff",
                        "Completed",
                    ),
                )
                run = request(new, "GET", "flow_runs/" + run_id)
                assert run["deployment_id"] == target["id"], name
                passed = run["state_type"] == "COMPLETED" and run["state_name"] == state
                qualification = {
                    "id": run_id,
                    "mode": mode,
                    "state_type": run["state_type"],
                    "state_name": run["state_name"],
                    "passed": passed,
                }
                if not passed:
                    pending.append(name + " qualification")
            elif name == "test-pull-patch":
                run = request(new, "GET", "flow_runs/9eec4780-9a47-4721-a423-d80feb751263")
                assert run["deployment_id"] == new_deployments["test-pull-boundary"]["id"]
                assert (
                    new_deployments["test-pull-boundary"]["job_variables"]["image"]
                    == target["job_variables"]["image"]
                )
                assert run["state_type"] == "COMPLETED"
                qualification = {
                    "id": run["id"],
                    "mode": "same immutable image; real sandbox boundary canary, not a production patch test",
                    "passed": True,
                }
            elif name == "typeahead-plc-identity":
                assert not any(s["active"] for s in target["schedules"])
                qualification = {
                    "mode": "paused definition and retained 55 GiB cache; no bulk replay or bandwidth incurred",
                    "passed": True,
                }
            else:
                pending.append("typeahead-index qualification")
            records.append(
                {
                    "name": name,
                    "old_id": source["id"],
                    "new_id": target["id"],
                    "pool": target["work_pool_name"],
                    "clocks": actual_clocks,
                    "qualification": qualification,
                }
            )
        old_rules = {a["name"]: a for a in request(old, "POST", "automations/filter", json={})}
        new_rules = {a["name"]: a for a in request(new, "POST", "automations/filter", json={})}
        for rule, name in RULES.items():
            assert not old_rules[rule]["enabled"], rule
            assert new_rules[rule]["enabled"], rule
            assert any(
                a.get("deployment_id") == new_deployments[name]["id"]
                for a in new_rules[rule]["actions"]
            ), rule
        if old_active:
            pending.append("original live/queued runs")
        for name in ("leaflet-atlas", "typeahead-index"):
            if not any(s["active"] for s in new_deployments[name]["schedules"]):
                pending.append(name + " clock activation")
        cloud_workers = request(
            new, "POST", "work_pools/typeahead-cloud-pool/workers/filter", json={}
        )
        cloud_workers = [
            {k: w[k] for k in ("name", "status", "last_heartbeat_time")} for w in cloud_workers
        ]
        if not any(w["status"] == "ONLINE" for w in cloud_workers):
            pending.append("HeavyPad cloud worker heartbeat")
        report = {
            "verified_at": datetime.now(UTC).isoformat(),
            "scope_count": len(SCOPE),
            "complete": not pending,
            "pending": pending,
            "original_live_runs": old_active,
            "cloud_workers": cloud_workers,
            "deployments": records,
            "gardener_execution_rules_verified": sorted(RULES),
            "canonical_routing_changed": False,
            "hetzner_deleted": False,
        }
        path = ROOT / "docs/audits/2026-10-27-remaining-migration-verification.json"
        path.write_text(json.dumps(report, indent=2) + "\n")
        print(
            json.dumps(
                {
                    "scope": len(SCOPE),
                    "complete": report["complete"],
                    "pending": pending,
                    "saved": str(path),
                }
            )
        )
        if args.require_complete and pending:
            raise SystemExit(1)


if __name__ == "__main__":
    main()
