from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

import boto3
import httpx

SLOTS = ("typeahead-startup-20261024", "typeahead-search-refresh")
ROUTE_KEY = "control/search-routing.json"
ROUTER = "https://typeahead-search-transport.n8-3e9.workers.dev"
PUBLIC = "https://typeahead.waow.tech"
OWNER = "typeahead-snapshot-refresh"
ARTIFACT_PREFIX = "releases/search/"


def origin(app: str) -> str:
    if app not in SLOTS:
        raise ValueError("Unmanaged refresh app")
    return f"https://{app}.fly.dev"


def route_target(route: dict) -> dict:
    target = route["current"]
    if route["version"] != 1 or target["origin"] not in [origin(s) for s in SLOTS]:
        raise ValueError("Unmanaged routing target")
    if not re.fullmatch(r"b[0-9]{10}-[A-Za-z0-9_-]+", target["build_id"]):
        raise ValueError("Invalid route identity")
    return target


class Refresh:
    def __init__(self, logger):
        self.logger = logger
        self.http = httpx.Client(timeout=30, headers={"User-Agent": "fleet-health/1"})
        self.s3 = boto3.client(
            "s3",
            endpoint_url=os.environ["INDEX_R2_ENDPOINT"],
            region_name="auto",
            aws_access_key_id=os.environ["INDEX_R2_ACCESS_KEY_ID"],
            aws_secret_access_key=os.environ["INDEX_R2_SECRET_ACCESS_KEY"],
        )
        self.bucket = os.environ["INDEX_R2_BUCKET"]
        self.image = os.environ["INDEX_REFRESH_IMAGE"]
        if not re.fullmatch(
            r"registry\.fly\.io/typeahead-search-refresh@sha256:[a-f0-9]{64}", self.image
        ):
            raise ValueError("An immutable qualified refresh image is required")

    def close(self):
        self.http.close()
        self.s3.close()

    def fly(self, method: str, app: str, path: str, body=None):
        origin(app)
        result = self.http.request(
            method,
            f"https://api.machines.dev/v1/apps/{app}/{path}",
            headers={
                "Authorization": "Bearer "
                + os.environ["FLY_PRIMARY_TOKEN" if app == SLOTS[0] else "FLY_REFRESH_TOKEN"]
            },
            json=body,
        )
        if result.status_code >= 400:
            raise RuntimeError(f"Fly {method} {app}/{path} failed: HTTP {result.status_code}")
        return result.json() if result.content else None

    def route(self):
        result = self.s3.get_object(Bucket=self.bucket, Key=ROUTE_KEY)
        raw = result["Body"].read(16385)
        if len(raw) > 16384:
            raise ValueError("Routing record too large")
        route = json.loads(raw)
        route_target(route)
        return route, result["ETag"]

    def replace_route(self, route: dict, etag: str):
        route_target(route)
        result = self.s3.put_object(
            Bucket=self.bucket,
            Key=ROUTE_KEY,
            Body=json.dumps(route, separators=(",", ":")).encode(),
            ContentType="application/json",
            IfMatch=etag,
        )
        return result["ETag"]

    def get(self, base: str, path: str):
        result = self.http.get(base + path, timeout=10)
        return result.status_code, result.json(), result.headers

    def healthy(self, target: dict, *, require_fresh: bool = True):
        code, health, _ = self.get(target["origin"], "/health")
        if code != 200 or not health["ready"] or (require_fresh and health["sync_lag_s"] >= 600):
            raise RuntimeError("Backend is not fresh and ready")
        code, index, _ = self.get(target["origin"], "/index/status")
        if (
            code != 200
            or index["build_id"] != target["build_id"]
            or index["source_watermark"] != target["source_watermark"]
        ):
            raise RuntimeError("Backend snapshot identity mismatch")
        if index.get("snapshot_format") != "FLATPX05":
            raise RuntimeError("Backend format mismatch")
        return {"health": health, "index": index}

    def serving_guard(self, expected: dict, etag: str, inactive_app: str):
        current, actual = self.route()
        if (
            actual != etag
            or current != expected
            or current["current"]["origin"] == origin(inactive_app)
        ):
            raise RuntimeError("Routing changed; refusing inactive resource mutation")
        self.healthy(current["current"], require_fresh=False)
        code, public, _ = self.get(PUBLIC, "/health/freshness")
        if (
            code not in (200, 503)
            or not public.get("ok")
            or not public.get("probe_ok")
            or public.get("build_id") != current["current"]["build_id"]
        ):
            raise RuntimeError("Public serving identity changed; refusing resource mutation")

    def delete_machine(self, app: str, machine: dict):
        mid = machine["id"]
        if machine["state"] != "stopped":
            self.fly("POST", app, f"machines/{mid}/stop", {"signal": "SIGTERM", "timeout": 60})
        for _ in range(20):
            state = self.fly("GET", app, f"machines/{mid}")["state"]
            if state in ("stopped", "destroyed"):
                break
            time.sleep(3)
        else:
            raise RuntimeError("Inactive machine did not stop cleanly")
        self.fly("DELETE", app, f"machines/{mid}")

    def discard_inactive(self, app: str, route: dict, etag: str):
        machines = self.fly("GET", app, "machines")
        previous = route.get("previous") or {}
        for machine in machines:
            self.serving_guard(route, etag, app)
            managed = machine["config"].get("metadata", {}).get("owner") == OWNER
            enrolled = machine["id"] == previous.get("machine_id") and origin(app) == previous.get(
                "origin"
            )
            if not (managed or enrolled):
                raise RuntimeError("Inactive app contains an unowned machine")
            volumes = [m["volume"] for m in machine["config"].get("mounts", [])]
            if enrolled and volumes != [previous["volume_id"]]:
                raise RuntimeError("Enrolled rollback volume changed")
            self.delete_machine(app, machine)
            for volume in volumes:
                self.serving_guard(route, etag, app)
                self.fly("DELETE", app, "volumes/" + volume)
        for volume in self.fly("GET", app, "volumes"):
            if (
                volume["state"] != "pending_destroy"
                and volume["name"].startswith("refresh_")
                and not volume.get("attached_machine_id")
            ):
                self.serving_guard(route, etag, app)
                self.fly("DELETE", app, "volumes/" + volume["id"])

    def candidate_checks(self, target: dict):
        status = self.healthy(target)
        timings = []
        for query in ["zzstoatzz.io", "a", "nate", "日本"]:
            start = time.monotonic()
            code, body, _ = self.get(target["origin"], "/search?q=" + query + "&limit=5")
            if code != 200 or not body.get("actors"):
                raise RuntimeError("Candidate semantic search probe failed")
            if query == "zzstoatzz.io" and body["actors"][0]["handle"] != query:
                raise RuntimeError("Candidate exact handle rank changed")
            timings.append(round((time.monotonic() - start) * 1000, 2))
        for pds in ["selfhosted.social", "https%3A%2F%2Fselfhosted.social", "unknown.invalid"]:
            code, body, headers = self.get(target["origin"], "/search?q=a&limit=5&pds=" + pds)
            expected = "unknown.invalid" if pds == "unknown.invalid" else "selfhosted.social"
            if (
                code != 200
                or headers.get("X-Typeahead-Pds") != expected
                or bool(body.get("actors")) != (expected != "unknown.invalid")
            ):
                raise RuntimeError("Candidate PDS search contract failed")
        code, body, _ = self.get(
            target["origin"], "/search?q=a&limit=5&pds=rhizopogon.us-west.host.bsky.network"
        )
        if code != 503 or body.get("error") != "PdsScopeUnavailable":
            raise RuntimeError("Excluded PDS must fail explicitly")
        if max(timings) >= 2500:
            raise RuntimeError("Candidate latency exceeds transport budget")
        return {"sync_lag_s": status["health"]["sync_lag_s"], "query_ms": timings}

    def watch(self, target: dict):
        observations = []
        for _ in range(5):
            code, fresh, _ = self.get(PUBLIC, "/health/freshness")
            if (
                code != 200
                or fresh["stale"]
                or not fresh["probe_ok"]
                or fresh["build_id"] != target["build_id"]
                or fresh["replica_lag_s"] >= 600
            ):
                raise RuntimeError("Public freshness failed after routing update")
            for alias in [
                "tech.waow.typeahead.searchActors",
                "app.bsky.actor.searchActorsTypeahead",
            ]:
                code, body, _ = self.get(PUBLIC, "/xrpc/" + alias + "?q=zzstoatzz.io&limit=1")
                if code != 200 or body["actors"][0]["handle"] != "zzstoatzz.io":
                    raise RuntimeError("Public search failed after routing update")
            observations.append(fresh)
            self.logger.info("New snapshot public verification: lag=%ds", fresh["replica_lag_s"])
            time.sleep(30)
        return observations

    def apply(self, generation: Path):
        manifest = json.loads((generation / "manifest.json").read_text())
        if (
            manifest["manifest_version"] != 2
            or manifest["format_version"] != 5
            or time.time() - manifest["source_watermark"] >= 3 * 86400
        ):
            raise ValueError("A fresh compact generation is required")
        route, etag = self.route()
        current = route_target(route)
        self.healthy(current, require_fresh=False)
        if current["build_id"] == manifest["active_build"]:
            return {"idempotent": True, "build_id": current["build_id"], "passed": True}
        status = self.healthy(current, require_fresh=False)["index"]
        if manifest["source_watermark"] < current["source_watermark"] or any(
            manifest[k] < status[k] // 2 for k in ["actor_count", "prefix_count"]
        ):
            raise ValueError("Snapshot regresses source watermark or corpus floors")
        app = next(s for s in SLOTS if origin(s) != current["origin"])
        self.discard_inactive(app, route, etag)
        pointer = "control/slots/" + app + "/latest.json"
        self.s3.put_object(
            Bucket=self.bucket,
            Key=pointer,
            Body=(generation / "manifest.json").read_bytes(),
            ContentType="application/json",
        )
        volume = self.fly(
            "POST",
            app,
            "volumes",
            {
                "name": "refresh_" + str(int(time.time())),
                "region": "ewr",
                "size_gb": 20,
                "encrypted": True,
                "auto_backup_enabled": False,
                "compute": {"cpu_kind": "shared", "cpus": 4, "memory_mb": 1024},
            },
        )
        machine = None
        next_route = None
        report = {
            "build_id": manifest["active_build"],
            "previous": current,
            "app": app,
            "volume_id": volume["id"],
        }
        try:
            config = {
                "image": self.image,
                "init": {"cmd": ["python3", "/app/install-snapshot.py"]},
                "env": {
                    "MODE": "search",
                    "INDEX_BUILD_ROOT": "/data/refresh",
                    "LOCAL_DB_PATH": "/data/refresh/overlay.db",
                    "INDEX_LATEST_KEY": pointer,
                    "REFRESH_BUILD_ID": manifest["active_build"],
                    "REFRESH_MIN_ACTOR_COUNT": str(status["actor_count"] // 2),
                    "REFRESH_MIN_PREFIX_COUNT": str(status["prefix_count"] // 2),
                    "INDEX_POLL_SECS": "300",
                },
                "guest": {"cpu_kind": "shared", "cpus": 4, "memory_mb": 1024},
                "mounts": [{"volume": volume["id"], "path": "/data"}],
                "restart": {"policy": "on-failure", "max_retries": 3},
                "metadata": {"owner": OWNER, "build_id": manifest["active_build"]},
                "services": [
                    {
                        "protocol": "tcp",
                        "internal_port": 8080,
                        "ports": [
                            {"port": 443, "handlers": ["tls", "http"]},
                            {"port": 80, "handlers": ["http"]},
                        ],
                        "autostart": False,
                        "autostop": False,
                    }
                ],
                "checks": {
                    "ready": {
                        "type": "http",
                        "port": 8080,
                        "method": "GET",
                        "path": "/ready",
                        "interval": "15s",
                        "timeout": "2s",
                        "grace_period": "30s",
                    }
                },
            }
            machine = self.fly(
                "POST",
                app,
                "machines",
                {
                    "name": "refresh-" + manifest["active_build"].lower(),
                    "region": "ewr",
                    "config": config,
                },
            )
            target = {
                "origin": origin(app),
                "build_id": manifest["active_build"],
                "source_watermark": manifest["source_watermark"],
                "machine_id": machine["id"],
                "volume_id": volume["id"],
                "legacy_build_id": manifest["legacy_twin"]["build_id"],
            }
            deadline = time.monotonic() + 2400
            while True:
                self.serving_guard(route, etag, app)
                try:
                    self.healthy(target)
                    break
                except (RuntimeError, httpx.HTTPError, ValueError, KeyError):
                    actual_machine = self.fly("GET", app, "machines/" + machine["id"])
                    if actual_machine["state"] in ("stopped", "destroyed"):
                        raise RuntimeError("Isolated installer stopped before readiness") from None
                    if time.monotonic() >= deadline:
                        raise RuntimeError(
                            "Isolated installation did not become healthy within 40 minutes"
                        ) from None
                    self.logger.info(
                        "Preparing %s on isolated machine %s; serving backend unchanged",
                        manifest["active_build"],
                        machine["id"],
                    )
                    time.sleep(20)
            report["pre_resize"] = self.candidate_checks(target)
            config["guest"]["cpus"] = 2
            self.fly("POST", app, "machines/" + machine["id"], {"config": config})
            for _ in range(30):
                try:
                    self.healthy(target)
                    break
                except (RuntimeError, httpx.HTTPError, ValueError, KeyError):
                    time.sleep(3)
            else:
                raise RuntimeError("Candidate failed cached restart at final size")
            report["qualified"] = self.candidate_checks(target)
            self.serving_guard(route, etag, app)
            next_route = {
                "version": 1,
                "current": target,
                "previous": current,
                "promoted_at": int(time.time()),
            }
            report["route_etag"] = self.replace_route(next_route, etag)
            time.sleep(2)
            report["observations"] = self.watch(target)
            report["current"] = target
            report["passed"] = True
            return report
        except BaseException:
            actual, actual_etag = self.route()
            if next_route is not None and actual == next_route:
                self.healthy(current, require_fresh=False)
                self.replace_route(route, actual_etag)
                self.logger.error("Restored previous verified backend after failed promotion")
            actual, _ = self.route()
            if actual["current"]["origin"] != origin(app):
                self.healthy(actual["current"], require_fresh=False)
                for candidate in self.fly("GET", app, "machines"):
                    metadata = candidate["config"].get("metadata", {})
                    if (
                        metadata.get("owner") == OWNER
                        and metadata.get("build_id") == manifest["active_build"]
                    ):
                        self.delete_machine(app, candidate)
                owned_volume = next(
                    (v for v in self.fly("GET", app, "volumes") if v["id"] == volume["id"]), None
                )
                if owned_volume is not None and not owned_volume.get("attached_machine_id"):
                    self.fly("DELETE", app, "volumes/" + volume["id"])
            raise

    def prune_artifacts(self):
        route, etag = self.route()
        keep = set()
        for entry in [route["current"], route.get("previous")]:
            if entry is not None:
                keep.add(entry["build_id"])
                keep.add(entry.get("legacy_build_id", entry["build_id"].removesuffix("-px05")))
        victims = []
        for page in self.s3.get_paginator("list_objects_v2").paginate(
            Bucket=self.bucket, Prefix=ARTIFACT_PREFIX
        ):
            for item in page.get("Contents", []):
                parts = item["Key"].split("/")
                if (
                    len(parts) == 4
                    and re.fullmatch(r"b[0-9]{10}-[A-Za-z0-9_-]+", parts[2])
                    and parts[2] not in keep
                    and parts[3] in ("index.db", "index.db.prefix", "manifest.json")
                ):
                    victims.append(item)
        for start in range(0, len(victims), 1000):
            current, actual_etag = self.route()
            if actual_etag != etag or current != route:
                raise RuntimeError("Routing changed; refusing artifact cleanup")
            result = self.s3.delete_objects(
                Bucket=self.bucket,
                Delete={
                    "Objects": [{"Key": item["Key"]} for item in victims[start : start + 1000]]
                },
            )
            if result.get("Errors"):
                raise RuntimeError("Managed artifact cleanup failed")
        return {
            "retained_builds": sorted(keep),
            "deleted_objects": len(victims),
            "deleted_bytes": sum(item["Size"] for item in victims),
        }


def refresh_snapshot(generation: Path):
    from prefect import get_run_logger

    refresh = Refresh(get_run_logger())
    try:
        report = refresh.apply(generation)
        output = (
            Path(os.environ["INDEXER_HOME"]) / "refresh-receipts" / (report["build_id"] + ".json")
        )
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2) + "\n")
        return report
    finally:
        refresh.close()


def prune_snapshot_artifacts():
    from prefect import get_run_logger, task

    @task(retries=3, retry_delay_seconds=[2, 5, 10], retry_jitter_factor=1)
    def prune():
        service = Refresh(get_run_logger())
        try:
            report = service.prune_artifacts()
            get_run_logger().info("Snapshot artifact cleanup: %s", report)
            return report
        finally:
            service.close()

    return prune()
