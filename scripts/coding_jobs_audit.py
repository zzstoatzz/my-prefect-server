#!/usr/bin/env -S uv run --script --quiet
# /// script
# requires-python = ">=3.13"
# dependencies = ["httpx"]
# ///
"""Read-only evidence inventory. Run with `just coding-jobs /tmp/coding-jobs`."""

import argparse
import json
import os
import re
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path

import httpx

FLOWS = {
    "autofix",
    "autofix-revise",
    "pi-agent",
    "pi-agent-local",
    "pi-pr",
    "merge-approved",
    "test-pull-patch",
    "test-pull-boundary",
    "pi-sprite-execution-probe",
    "phi-sprites-uv-smoke",
    "phi-sprites-worker-smoke",
    "dep-bump",
}
ACTORS = {
    "gardener": "did:plc:7vx7exykq2zfxjxxejovrymi",
    "phi": "did:plc:65sucjiel52gefhcdcypynsr",
    "owner": "did:plc:xbtmt2zjwlrfegqvch7fboei",
}
PULL = re.compile(r"at://[^\s\"'<>]+/sh\.tangled\.repo\.pull/[a-z0-9]+")


def pages(client, base, endpoint, filters=None):
    """Page until empty; a short page may be a server-side cap."""
    rows, seen = [], set()
    while True:
        response = client.post(
            f"{base}/{endpoint}/filter",
            json={
                **(filters or {}),
                "limit": 200,
                "offset": len(rows),
            },
        )
        response.raise_for_status()
        batch = response.json()
        if not batch:
            return rows
        ids = {r["id"] for r in batch}
        if seen.intersection(ids):
            raise RuntimeError(f"Unstable or ignored pagination: {endpoint}")
        seen.update(ids)
        rows.extend(batch)


def records(client, did, collection):
    doc = client.get(f"https://plc.directory/{did}")
    doc.raise_for_status()
    pds = next(
        s["serviceEndpoint"]
        for s in doc.json()["service"]
        if s["type"] == "AtprotoPersonalDataServer"
    )
    result, cursor, seen = [], None, set()
    while True:
        params = {"repo": did, "collection": collection, "limit": 100}
        if cursor:
            params["cursor"] = cursor
        response = client.get(f"{pds}/xrpc/com.atproto.repo.listRecords", params=params)
        response.raise_for_status()
        page = response.json()
        result.extend(page["records"])
        cursor = page.get("cursor")
        if not cursor or not page["records"]:
            return result
        if cursor in seen:
            raise RuntimeError("Repeated PDS cursor")
        seen.add(cursor)


def cost_summary(records):
    unique = {r["id"]: r for r in records if r.get("id")}
    rows = list(unique.values())
    # Historical zero-price sentinels are unknown, not evidence of free usage.
    priced = [
        r
        for r in rows
        if isinstance(r.get("total_cost_usd"), (int, float))
        and (r.get("cost_basis") == "catalog_estimate" or r["total_cost_usd"] > 0)
    ]
    return {
        "usage_records": len(rows),
        "priced_records": len(priced),
        "unpriced_records": len(rows) - len(priced),
        "known_estimated_cost_usd": sum(r["total_cost_usd"] for r in priced) if priced else None,
        "billed_cost_usd": None,
        "tokens": sum(r.get("total_tokens", 0) for r in rows) if rows else None,
        "tasks": sorted({r.get("task_name", "unknown") for r in rows}),
    }


def summarize(run, flow, logs, artifacts, ui):
    messages = [r.get("message", "") for r in logs]
    usage = []
    for message in messages:
        if "pi_usage " in message:
            with suppress(ValueError):
                usage.append(json.loads(message.split("pi_usage ", 1)[1]))
    spend, executions = [], []
    for message in messages:
        for prefix, target in (("llm_usage ", spend), ("pi_execution ", executions)):
            if message.startswith(prefix):
                with suppress(ValueError):
                    target.append(json.loads(message[len(prefix) :]))
    params = run.get("parameters") or {}
    # Do not export raw parameters, prompts, logs, artifact bodies or state messages.
    text = "\n".join(messages) + json.dumps(params)
    return {
        "id": run["id"],
        "flow": flow,
        "name": run["name"],
        "created": run["created"],
        "state": run.get("state_name"),
        "state_type": run.get("state_type"),
        "deployment_id": run.get("deployment_id"),
        "telemetry_probe": "telemetry-proof" in (run.get("tags") or []),
        "url": f"{ui}/runs/flow-run/{run['id']}",
        "seconds": run.get("total_run_time"),
        "log_count": len(logs),
        "artifacts": [
            {"id": a["id"], "key": a.get("key"), "type": a.get("type")} for a in artifacts
        ],
        "pulls": sorted(set(PULL.findall(text))),
        "source_run_id": params.get("flow_run_id"),
        "dry_run": params.get("dry_run"),
        "propose": params.get("propose"),
        "propose_for": params.get("propose_for"),
        "tool_events": sum("pi_tool " in m for m in messages),
        "usage_events": len(usage),
        "inference_cost": cost_summary(spend),
        "usage_record_ids": [r["id"] for r in spend],
        "pi_invocations": len(executions),
        "pi_usage_complete": all(e.get("usage_complete") for e in executions)
        if executions
        else None,
        "_spend": spend,
        "tokens": sum(u.get("totalTokens", 0) for u in usage) if usage else None,
        "policy_rejected": any("prompt rejected by policy judge" in m for m in messages),
    }


def markdown(report):
    rows = report["runs"]
    lines = [
        "# Coding jobs evidence",
        "",
        f"Fetched {report['as_of']}.",
        "",
        "Read-only inventory of retained Prefect runs and public PDS records. "
        "Completed is execution state, not proof of a useful fix. Missing telemetry is unknown, not zero.",
        "",
        "## Coverage",
        "",
        f"Flows: {', '.join(sorted(FLOWS))}, plus phi-pull-review.",
        "All filter endpoints paginated to empty; PDS records paginated to exhaustion. "
        "Deleted runs and direct CLI jobs cannot be reconstructed from Prefect. "
        "No automatic claim of shipped code: verify pulls against Git.",
        "",
        f"Runs: {len(rows)}; with logs: {sum(bool(r['log_count']) for r in rows)}; "
        f"with usage evidence (including judge-only): "
        f"{sum(bool(r['usage_events'] or r['inference_cost']['usage_records']) for r in rows)}.",
        "",
        "| Flow | Runs | States |",
        "| --- | ---: | --- |",
    ]
    for flow in sorted({r["flow"] for r in rows}):
        subset = [r for r in rows if r["flow"] == flow]
        counts = Counter(r["state"] for r in subset)
        lines.append(
            f"| {flow} | {len(subset)} | "
            + ", ".join(f"{k}: {v}" for k, v in sorted(counts.items()))
            + " |"
        )
    lines += [
        "",
        "## Durable pulls",
        "",
        "Statuses are owner PDS records, not proof of Git inclusion. "
        "A missing status is unknown. Reviews below must match the current CID and round.",
        "",
    ]
    for pull in report["pulls"]:
        lines += [
            f"- {pull['title']} — {pull['actor']}, {pull['rounds']} rounds; "
            f"status: {pull['status']}; current review: {pull['current_review']}",
            f"  `{pull['uri']}`",
        ]
    lines += [
        "",
        "## Every retained run",
        "",
        "Costs below are known inference estimates, excluding unpriced requests, interrupted responses, "
        "Sprite compute, and subscription allocation. Judge-only coverage is not total job cost.",
        "",
        "| Run | Flow / state | Logs | Artifacts | Estimated USD / priced records / tasks | Pi coverage |",
        "| --- | --- | ---: | ---: | --- | --- |",
    ]
    for run in rows:
        cost = run["inference_cost"]
        dollars = cost["known_estimated_cost_usd"]
        label = f"{dollars:.6f}" if dollars is not None else "unknown"
        coverage = {True: "complete", False: "partial", None: "unknown"}[run["pi_usage_complete"]]
        probe = " (telemetry probe)" if run["telemetry_probe"] else ""
        lines.append(
            f"| [{run['id']}]({run['url']}) | {run['flow']} / {run['state']}{probe} | "
            f"{run['log_count']} | {len(run['artifacts'])} | {label} / {cost['priced_records']} / {', '.join(cost['tasks']) or 'unknown'} | {coverage} |"
        )
    return "\n".join(lines) + "\n"


def collect(base, auth, recovered=None):
    with httpx.Client(auth=auth, timeout=60) as client:
        flows = {f["id"]: f["name"] for f in pages(client, base, "flows")}
        deployments = pages(client, base, "deployments")
        selected = [id for id, name in flows.items() if name in FLOWS]
        runs = pages(client, base, "flow_runs", {"flows": {"id": {"any_": selected}}})
        review_ids = [d["id"] for d in deployments if d["name"] == "phi-pull-review"]
        if review_ids:
            runs += pages(client, base, "flow_runs", {"deployments": {"id": {"any_": review_ids}}})
        runs = list({r["id"]: r for r in runs}.values())

        def inspect(run):
            logs = pages(client, base, "logs", {"logs": {"flow_run_id": {"any_": [run["id"]]}}})
            artifacts = pages(
                client, base, "artifacts", {"artifacts": {"flow_run_id": {"any_": [run["id"]]}}}
            )
            return summarize(run, flows[run["flow_id"]], logs, artifacts, base.removesuffix("/api"))

        with ThreadPoolExecutor(max_workers=6) as pool:
            inventory = list(pool.map(inspect, runs))
    for run in inventory:
        records_for_run = run.pop("_spend") + [
            r for r in (recovered or []) if r.get("flow_run_id") == run["id"]
        ]
        run["inference_cost"] = cost_summary(records_for_run)
        run["usage_record_ids"] = sorted({r["id"] for r in records_for_run})
    # Separate unauthenticated client: never send Prefect credentials to a PDS.
    with httpx.Client(timeout=30) as public:
        statuses = records(public, ACTORS["owner"], "sh.tangled.repo.pull.status")
        comments = records(public, ACTORS["phi"], "sh.tangled.feed.comment")
        pulls = []
        for actor in ("gardener", "phi"):
            for record in records(public, ACTORS[actor], "sh.tangled.repo.pull"):
                value = record["value"]
                matching = [s["value"] for s in statuses if s["value"].get("pull") == record["uri"]]
                status = max(matching, key=lambda s: s.get("createdAt", "")) if matching else {}
                reviews = []
                for c in comments:
                    cv = c["value"]
                    subject = cv.get("subject") or {}
                    if not isinstance(subject, dict):
                        continue
                    if (
                        subject.get("uri") == record["uri"]
                        and subject.get("cid") == record["cid"]
                        and cv.get("pullRoundIdx") == len(value.get("rounds", [])) - 1
                    ):
                        body = cv.get("body", "")
                        text = body.get("text", "") if isinstance(body, dict) else body
                        verdict = re.search(
                            r"VERDICT:\s*(approve|request-changes|escalate)", text, re.I
                        )
                        if verdict:
                            reviews.append((cv.get("createdAt", ""), verdict[1].lower()))
                pulls.append(
                    {
                        "uri": record["uri"],
                        "cid": record["cid"],
                        "actor": actor,
                        "title": value["title"],
                        "created": value.get("createdAt"),
                        "target": value.get("target"),
                        "rounds": len(value.get("rounds", [])),
                        "status": status.get("status", "unknown"),
                        "current_review": max(reviews)[1] if reviews else "unknown",
                    }
                )
    return {
        "as_of": datetime.now(UTC).isoformat(),
        "runs": sorted(inventory, key=lambda r: r["created"], reverse=True),
        "pulls": pulls,
        "recovered_spend": cost_summary(recovered or []),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument(
        "--spend-log", type=Path, help="Optional recovered JSONL; deduplicated by event ID"
    )
    args = parser.parse_args()
    base = os.environ["PREFECT_API_URL"].rstrip("/")
    username, sep, password = os.environ["PREFECT_API_AUTH_STRING"].partition(":")
    if not sep:
        raise ValueError("Expected user:password authentication")
    recovered_path = args.spend_log or args.output / "recovered-spend.jsonl"
    recovered = (
        [json.loads(line) for line in recovered_path.read_text().splitlines() if line.strip()]
        if args.spend_log or recovered_path.exists()
        else []
    )
    report = collect(base, httpx.BasicAuth(username, password), recovered)
    os.umask(0o077)
    args.output.mkdir(parents=True, exist_ok=True)
    for name, data in (
        ("report.json", json.dumps(report, indent=2)),
        ("report.md", markdown(report)),
    ):
        path = args.output / name
        path.write_text(data)
        path.chmod(0o600)
    print(
        f"Inventoried {len(report['runs'])} runs and {len(report['pulls'])} pulls: {args.output / 'report.md'}"
    )


if __name__ == "__main__":
    main()
