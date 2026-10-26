"""Verify public cost publication against a completed run's table artifact."""

import argparse
import json
from collections import Counter, defaultdict
from contextlib import ExitStack
from datetime import datetime, timedelta
from decimal import Decimal

import httpx
from prefect.context import use_profile
from prefect.settings import PREFECT_API_AUTH_STRING, PREFECT_API_KEY, PREFECT_API_URL, temporary_settings

from recovery_secret_blocks import CLOUD, extract


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target", choices=["cloud", "heavypad"])
    parser.add_argument("run_id")
    args = parser.parse_args()
    canonical = json.loads(extract('["prefect"]["blocks"]'))["operator-atproto-creds"]
    if isinstance(canonical, str):
        canonical = json.loads(canonical)
    if "value" in canonical and set(canonical) <= {"value", "__prefect_kind"}:
        canonical = canonical["value"]
        if isinstance(canonical, str):
            canonical = json.loads(canonical)
    with ExitStack() as contexts:
        if args.target == "cloud":
            contexts.enter_context(use_profile("bot"))
            contexts.enter_context(temporary_settings({PREFECT_API_URL: CLOUD, PREFECT_API_AUTH_STRING: None}))
            client = contexts.enter_context(httpx.Client(base_url=CLOUD + "/", headers={"Authorization": f"Bearer {PREFECT_API_KEY.value()}"}, timeout=30))
        else:
            auth = json.loads(extract('["heavypad_prefect"]'))["api_auth_string"]
            client = contexts.enter_context(httpx.Client(base_url="https://heavypad.tailb660b6.ts.net:10443/api/", auth=tuple(auth.split(":", 1)), timeout=30))

        def request(method, path, **kwargs):
            response = client.request(method, path, **kwargs)
            response.raise_for_status()
            return response.json()

        run = request("GET", f"flow_runs/{args.run_id}")
        assert run["state_type"] == "COMPLETED", "Publication run is not completed"
        assert run["parameters"]["config"]["dry_run"] is False, "Run was read-only"
        artifacts = request("POST", "artifacts/filter", json={"artifacts": {"flow_run_id": {"any_": [args.run_id]}}})
        tables = [a for a in artifacts if a["key"] == "cost-snapshot"]
        assert len(tables) == 1, "Expected exactly one cost artifact"
        rows = tables[0]["data"]
        if isinstance(rows, str):
            rows = json.loads(rows)
        assert rows, "Cost artifact is empty"
        start = datetime.fromisoformat(run["start_time"].replace("Z", "+00:00")) - timedelta(seconds=5)
        end = datetime.fromisoformat(run["end_time"].replace("Z", "+00:00")) + timedelta(seconds=5)
        days = {start.date().isoformat(), end.date().isoformat()}
        matches = []
        for day in sorted(days):
            response = httpx.get(canonical["pds"].rstrip("/") + "/xrpc/com.atproto.repo.getRecord", params={"repo": canonical["handle"], "collection": "io.zzstoatzz.cost.snapshot", "rkey": day}, timeout=30)
            if response.status_code == 400 and response.json().get("error") == "RecordNotFound":
                continue
            response.raise_for_status()
            record = response.json()["value"]
            generated = datetime.fromisoformat(record["generatedAt"].replace("Z", "+00:00"))
            if start <= generated <= end:
                matches.append((day, record))
        assert len(matches) == 1, "No unique public record generated during this run"
        day, record = matches[0]
        assert record["$type"] == "io.zzstoatzz.cost.snapshot"
        assert record["currency"] == "USD"
        expected = Counter((r["provider"], r["project"], r["service"], int(Decimal(str(r["usd"])) * 100), r["estimated"], r["note"]) for r in rows)
        actual = Counter((r["provider"], r["project"], r["service"], r["amount"], r["estimated"], r.get("note", "")) for r in record["lineItems"])
        assert actual == expected, "Public line items differ from the run artifact"
        assert sum(r["amount"] for r in record["lineItems"]) == record["total"]
        for field, source in (("byProvider", "provider"), ("byProject", "project")):
            groups = defaultdict(lambda: {"amount": 0, "estimated": False})
            for item in record["lineItems"]:
                groups[item[source]]["amount"] += item["amount"]
                groups[item[source]]["estimated"] |= item["estimated"]
            assert len(record[field]) == len(groups)
            assert {r["key"]: {"amount": r["amount"], "estimated": r["estimated"]} for r in record[field]} == dict(groups), f"Incorrect {field} rollup"
        print(f"Verified public costs {day}: {len(rows)} items, ${record['total'] / 100:.2f}; line items, rollups and run timestamp match; state={run['state_name']}")


if __name__ == "__main__":
    main()
