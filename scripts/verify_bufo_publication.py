"""Read public PDS records and compare them to a completed run's table artifact."""

import argparse
import json
from contextlib import ExitStack
from datetime import datetime, timedelta

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
        artifacts = request("POST", "artifacts/filter", json={"artifacts": {"flow_run_id": {"any_": [args.run_id]}}})
        tables = [a for a in artifacts if a["key"] == "bufo-traffic"]
        assert len(tables) == 1, "Expected exactly one traffic artifact"
        rows = tables[0]["data"]
        if isinstance(rows, str):
            rows = json.loads(rows)
        assert rows, "Publication has no expected day records"
        start = datetime.fromisoformat(run["start_time"].replace("Z", "+00:00")) - timedelta(seconds=5)
        end = datetime.fromisoformat(run["end_time"].replace("Z", "+00:00")) + timedelta(seconds=5)
        for row in rows:
            response = httpx.get(canonical["pds"].rstrip("/") + "/xrpc/com.atproto.repo.getRecord", params={"repo": canonical["handle"], "collection": "com.find-bufo.traffic", "rkey": row["day"]}, timeout=30)
            response.raise_for_status()
            record = response.json()["value"]
            assert record["day"] == row["day"]
            assert record["total"] == row["requests"]
            assert record["byRoute"].get("/api/search", 0) == row["search"]
            assert record["byRoute"].get("/e/{name}", 0) == row["images (/e/)"]
            assert record["byStatus"].get("5xx", 0) == row["5xx"]
            assert sum(record["hours"]) == record["total"]
            for key in ("byRoute", "byStatus", "byClient"):
                assert sum(record[key].values()) == record["total"]
            generated = datetime.fromisoformat(record["generatedAt"].replace("Z", "+00:00"))
            assert start <= generated <= end, "PDS record was not generated during this run"
            print(f"Verified public day {row['day']}: {record['total']} requests; run timestamp and aggregates match")


if __name__ == "__main__":
    main()
