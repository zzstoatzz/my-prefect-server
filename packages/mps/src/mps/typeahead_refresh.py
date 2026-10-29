from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

import boto3
import httpx
from prefect import task

SLOTS = ("typeahead-startup-20261024", "typeahead-search-refresh")
ROUTE_KEY = "control/search-routing.json"
ROUTER = "https://typeahead-search-transport.n8-3e9.workers.dev"
PUBLIC = "https://typeahead.waow.tech"
OWNER = "typeahead-snapshot-refresh"
ARTIFACT_PREFIX = "releases/search/"
TANTIVY_ORIGIN = "https://typeahead-tantivy-dev.fly.dev"
TANTIVY_FORMAT = "tantivy-0.27-f32-did-expiry-v4"
BUILD_PATTERN = r"b[0-9]{10}-[A-Za-z0-9_-]+"


def origin(app: str) -> str:
    if app not in SLOTS:
        raise ValueError("Unmanaged refresh app")
    return f"https://{app}.fly.dev"


def target_engine(target: dict) -> str:
    engine = target.get("engine", "native")
    if engine not in ("native", "tantivy"):
        raise ValueError("Unsupported route engine")
    return engine


def route_target(route: dict) -> dict:
    target = route["current"]
    if type(route["version"]) is not int or route["version"] != 1 or not isinstance(target, dict):
        raise ValueError("Invalid routing record")
    if not isinstance(target.get("build_id"), str) or not re.fullmatch(
        BUILD_PATTERN, target["build_id"]
    ):
        raise ValueError("Invalid route identity")
    if target_engine(target) == "native":
        if target["origin"] not in [origin(slot) for slot in SLOTS]:
            raise ValueError("Unmanaged routing target")
    elif (
        target.get("origin") != TANTIVY_ORIGIN
        or target.get("format") != TANTIVY_FORMAT
        or target.get("retrieval_strategy") not in ("per-segment", "global-merge")
        or any(
            not isinstance(target.get(key), str) or not re.fullmatch(r"[a-f0-9]{64}", target[key])
            for key in ("admission_sha256", "binary_sha256")
        )
    ):
        raise ValueError("Invalid Tantivy route identity")
    return target


def require_native_refresh(target: dict):
    if target_engine(target) != "native":
        raise RuntimeError(
            "Tantivy serving: automatic native installation is fenced pending engine-specific refresh qualification"
        )


def validate_route_record(route: dict):
    route_target(route)
    for field in ("previous", "native_rollback"):
        if route.get(field) is not None:
            target = route_target({"version": 1, "current": route[field]})
            if field == "native_rollback" and target_engine(target) != "native":
                raise ValueError("Rollback anchor must be native")


def native_rollback_target(route: dict) -> dict:
    validate_route_record(route)
    target = (
        route.get("native_rollback") or route.get("previous")
        if target_engine(route["current"]) == "tantivy"
        else route.get("previous")
    )
    if target is None:
        raise RuntimeError("No retained native rollback backend is available")
    require_native_refresh(target)
    return target


def response_identity(target: dict, body: dict, headers):
    if target_engine(target) != "tantivy":
        return
    expected = {
        "engine": "tantivy",
        "build_id": target["build_id"],
        "format": target["format"],
        "binary_sha256": target["binary_sha256"],
        "retrieval_strategy": target["retrieval_strategy"],
    }
    transport = {
        "engine": "X-Typeahead-Engine",
        "build_id": "X-Typeahead-Build",
        "format": "X-Typeahead-Format",
        "binary_sha256": "X-Typeahead-Binary-Sha256",
        "retrieval_strategy": "X-Typeahead-Retrieval-Strategy",
    }
    if any(
        body.get(key) != value or headers.get(transport[key]) != value
        for key, value in expected.items()
    ):
        raise RuntimeError("Tantivy backend/transport identity mismatch")


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
        validate_route_record(route)
        return route, result["ETag"]

    def replace_route(self, route: dict, etag: str):
        require_native_refresh(route_target(route))
        return self._put_route(route, etag)

    def _put_route(self, route: dict, etag: str):
        validate_route_record(route)
        result = self.s3.put_object(
            Bucket=self.bucket,
            Key=ROUTE_KEY,
            Body=json.dumps(route, separators=(",", ":")).encode(),
            ContentType="application/json",
            IfMatch=etag,
        )
        return result["ETag"]

    def _restore_previous_route(self, previous: dict, failed: dict, etag: str):
        actual, actual_etag = self.route()
        if actual != failed or actual_etag != etag:
            raise RuntimeError("Routing changed; refusing compensating rollback")
        self.healthy(previous["current"], require_fresh=False)
        return self._put_route(previous, etag)

    def get(self, base: str, path: str):
        result = self.http.get(base + path, timeout=10)
        return result.status_code, result.json(), result.headers

    def healthy(self, target: dict, *, require_fresh: bool = True):
        code, health, health_headers = self.get(target["origin"], "/health")
        if (
            code != 200
            or not health["ready"]
            or (require_fresh and not 0 <= health["sync_lag_s"] < 600)
        ):
            raise RuntimeError("Backend is not fresh and ready")
        code, index, index_headers = self.get(target["origin"], "/index/status")
        if (
            code != 200
            or index["build_id"] != target["build_id"]
            or (
                target_engine(target) == "native"
                and index["source_watermark"] != target["source_watermark"]
            )
            or (
                target_engine(target) == "tantivy"
                and "source_watermark" in target
                and index.get("snapshot_watermark") != target["source_watermark"]
            )
        ):
            raise RuntimeError("Backend snapshot identity mismatch")
        if target_engine(target) == "tantivy":
            if index.get("ready") is not True:
                raise RuntimeError("Tantivy index is not ready")
            response_identity(target, index, index_headers)
            for key, header in [
                ("engine", "X-Typeahead-Engine"),
                ("build_id", "X-Typeahead-Build"),
                ("format", "X-Typeahead-Format"),
                ("binary_sha256", "X-Typeahead-Binary-Sha256"),
                ("retrieval_strategy", "X-Typeahead-Retrieval-Strategy"),
            ]:
                wanted = "tantivy" if key == "engine" else target[key]
                if health_headers.get(header) != wanted:
                    raise RuntimeError("Tantivy health transport identity mismatch")
            if health.get("engine") != "tantivy" or health.get("build_id") != target["build_id"]:
                raise RuntimeError("Tantivy health identity mismatch")
        elif index.get("snapshot_format") != "FLATPX05":
            raise RuntimeError("Backend format mismatch")
        return {"health": health, "index": index}

    def serving_guard(self, expected: dict, etag: str, inactive_app: str):
        current, actual = self.route()
        require_native_refresh(route_target(current))
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
        if machine["state"] == "destroyed":
            return
        if machine["state"] == "created":
            self.fly("DELETE", app, f"machines/{mid}?force=true")
            return
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
        require_native_refresh(route_target(route))
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

    def wait_machine(self, app: str, mid: str, state: str, *, guest=None):
        deadline = time.monotonic() + 180
        while True:
            machine = self.fly("GET", app, "machines/" + mid)
            if machine["state"] == state and (
                guest is None
                or all(machine["config"]["guest"].get(k) == v for k, v in guest.items())
            ):
                return machine
            if time.monotonic() >= deadline:
                raise RuntimeError("Isolated machine did not reach " + state)
            time.sleep(3)

    def restart_candidate(self, app: str, mid: str, config: dict, route: dict, etag: str):
        before = self.fly("GET", app, "machines/" + mid)
        self.serving_guard(route, etag, app)
        if before["state"] != "stopped":
            try:
                self.fly(
                    "POST", app, "machines/" + mid + "/stop", {"signal": "SIGTERM", "timeout": 60}
                )
            except httpx.TransportError:
                self.logger.warning("Stop response was ambiguous; checking actual machine state")
        stopped = self.wait_machine(app, mid, "stopped")
        config["guest"] = {"cpu_kind": "shared", "cpus": 2, "memory_mb": 1024}
        self.serving_guard(route, etag, app)
        try:
            self.fly(
                "POST",
                app,
                "machines/" + mid,
                {
                    "config": config,
                    "skip_launch": True,
                    "current_version": stopped["instance_id"],
                },
            )
        except httpx.TransportError:
            self.logger.warning("Update response was ambiguous; checking actual configuration")
        self.wait_machine(app, mid, "stopped", guest=config["guest"])
        self.serving_guard(route, etag, app)
        try:
            self.fly("POST", app, "machines/" + mid + "/start")
        except httpx.TransportError:
            self.logger.warning("Start response was ambiguous; checking actual machine state")
        started = self.wait_machine(app, mid, "started", guest=config["guest"])
        return {
            "stopped_before_update": True,
            "started_after_update": True,
            "before_instance": before["instance_id"],
            "after_instance": started["instance_id"],
        }

    def accept_candidate(
        self, target: dict, config: dict, report: dict, route: dict, etag: str, app: str
    ):
        report["pre_resize"] = self.candidate_checks(target)
        report["restart"] = self.restart_candidate(app, target["machine_id"], config, route, etag)
        deadline = time.monotonic() + 180
        while True:
            try:
                self.healthy(target)
                break
            except (RuntimeError, httpx.HTTPError, ValueError, KeyError):
                if time.monotonic() >= deadline:
                    raise RuntimeError("Candidate failed cached restart at final size") from None
                time.sleep(3)
        report["qualified"] = self.candidate_checks(target)
        self.serving_guard(route, etag, app)
        next_route = {
            "version": 1,
            "current": target,
            "previous": route["current"],
            "promoted_at": int(time.time()),
        }
        try:
            report["route_etag"] = self.replace_route(next_route, etag)
            time.sleep(2)
            report["observations"] = self.watch(target)
            report["current"] = target
            report["passed"] = True
            return report
        except BaseException:
            actual, actual_etag = self.route()
            if actual == next_route:
                self.healthy(route["current"], require_fresh=False)
                self._restore_previous_route(route, next_route, actual_etag)
                self.logger.error("Restored previous verified backend after failed promotion")
            raise

    def release_is_current(self, target: dict, manifest: dict) -> bool:
        require_native_refresh(target)
        if target["build_id"] != manifest["active_build"]:
            return False
        app = next(app for app in SLOTS if origin(app) == target["origin"])
        machine = self.fly("GET", app, "machines/" + target["machine_id"])
        return machine["config"]["image"] == self.image

    def resume_published(self, expected: dict | None = None):
        import tempfile

        response = self.s3.get_object(Bucket=self.bucket, Key="control/builds/latest.json")
        raw = response["Body"].read(65537)
        if len(raw) > 65536:
            raise ValueError("Published manifest too large")
        manifest = json.loads(raw)
        if expected is not None and manifest != expected:
            raise RuntimeError("Published generation changed before rollout")
        if (
            manifest["manifest_version"] != 2
            or manifest["format_version"] != 5
            or not 0 <= time.time() - manifest["source_watermark"] < 3 * 86400
        ):
            raise ValueError("Published compact snapshot is not fresh")
        route, etag = self.route()
        require_native_refresh(route_target(route))
        if self.release_is_current(route["current"], manifest):
            with tempfile.TemporaryDirectory(prefix="typeahead-resume-") as directory:
                generation = Path(directory)
                (generation / "manifest.json").write_bytes(raw)
                return self.apply(generation)
        serving = self.healthy(route["current"], require_fresh=False)["index"]
        if manifest["source_watermark"] < route["current"]["source_watermark"] or any(
            manifest[key] < serving[key] // 2 for key in ["actor_count", "prefix_count"]
        ):
            raise ValueError("Published snapshot regresses serving watermark or corpus floors")
        if (
            not manifest["snapshot_url"].startswith(ARTIFACT_PREFIX)
            or manifest["prefix_map"]["object_key"] != manifest["snapshot_url"] + ".prefix"
        ):
            raise ValueError("Published snapshot is outside managed artifacts")
        app = next(s for s in SLOTS if origin(s) != route["current"]["origin"])
        self.serving_guard(route, etag, app)
        machines = self.fly("GET", app, "machines")
        matching = [
            m
            for m in machines
            if m["config"].get("metadata", {}).get("owner") == OWNER
            and m["config"].get("metadata", {}).get("build_id") == manifest["active_build"]
        ]
        if len(machines) == 1 and len(matching) == 1:
            machine = matching[0]
            config = machine["config"]
            if (
                config["image"] != self.image
                or config["env"].get("REFRESH_BUILD_ID") != manifest["active_build"]
                or config["env"].get("INDEX_LATEST_KEY") != "control/slots/" + app + "/latest.json"
                or len(config.get("mounts", [])) != 1
            ):
                raise RuntimeError("Prepared candidate configuration changed")
            pointer = self.s3.get_object(Bucket=self.bucket, Key=config["env"]["INDEX_LATEST_KEY"])[
                "Body"
            ].read(65537)
            if json.loads(pointer) != manifest:
                raise RuntimeError("Prepared candidate publication identity changed")
            if machine["state"] == "stopped":
                self.fly("POST", app, "machines/" + machine["id"] + "/start")
            target = {
                "origin": origin(app),
                "build_id": manifest["active_build"],
                "source_watermark": manifest["source_watermark"],
                "machine_id": machine["id"],
                "volume_id": config["mounts"][0]["volume"],
                "legacy_build_id": manifest["legacy_twin"]["build_id"],
            }
            deadline = time.monotonic() + 2400
            while True:
                self.serving_guard(route, etag, app)
                try:
                    self.healthy(target)
                    break
                except (RuntimeError, httpx.HTTPError, ValueError, KeyError):
                    if self.fly("GET", app, "machines/" + machine["id"])["state"] in (
                        "stopped",
                        "destroyed",
                    ):
                        raise RuntimeError("Prepared candidate stopped before readiness") from None
                    if time.monotonic() >= deadline:
                        raise RuntimeError("Prepared candidate did not become healthy") from None
                    time.sleep(20)
            report = {
                "build_id": target["build_id"],
                "app": app,
                "previous": route["current"],
                "volume_id": target["volume_id"],
                "resumed_prepared_candidate": True,
            }
            return self.accept_candidate(target, config, report, route, etag, app)
        with tempfile.TemporaryDirectory(prefix="typeahead-resume-") as directory:
            generation = Path(directory)
            (generation / "manifest.json").write_bytes(raw)
            return self.apply(generation)

    def apply(self, generation: Path):
        manifest = json.loads((generation / "manifest.json").read_text())
        if (
            manifest["manifest_version"] != 2
            or manifest["format_version"] != 5
            or not 0 <= time.time() - manifest["source_watermark"] < 3 * 86400
        ):
            raise ValueError("A fresh compact generation is required")
        route, etag = self.route()
        current = route_target(route)
        require_native_refresh(current)
        self.healthy(current, require_fresh=False)
        if self.release_is_current(current, manifest):
            return {
                "idempotent": True,
                "build_id": current["build_id"],
                "current": current,
                "previous": route.get("previous"),
                "qualified": self.candidate_checks(current),
                "observations": self.watch(current),
                "passed": True,
            }
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
                "stop_config": {"signal": "SIGTERM", "timeout": 60000000000},
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
            return self.accept_candidate(target, config, report, route, etag, app)
        except BaseException:
            self.logger.error(
                "Prepared candidate retained in inactive app %s for inspection or resume_rollout",
                app,
            )
            raise

    def rollback_to_native(self):
        route, etag = self.route()
        previous = native_rollback_target(route)
        self.healthy(previous, require_fresh=False)
        replacement = {
            "version": 1,
            "current": previous,
            "previous": route["current"],
            "promoted_at": int(time.time()),
            "rollback": True,
        }
        if route.get("native_rollback") is not None:
            replacement["native_rollback"] = route["native_rollback"]
        self.replace_route(replacement, etag)
        time.sleep(2)
        try:
            code, freshness, _ = self.get(PUBLIC, "/health/freshness")
            if (
                code not in (200, 503)
                or not freshness.get("ok")
                or not freshness.get("probe_ok")
                or freshness.get("build_id") != previous["build_id"]
            ):
                raise RuntimeError("Rollback public verification failed")
            self.candidate_checks(previous)
        except BaseException:
            actual, actual_etag = self.route()
            if actual == replacement:
                self._restore_previous_route(route, replacement, actual_etag)
            raise
        report = {
            "at_unix": int(time.time()),
            "current": previous,
            "previous": route["current"],
            "freshness": freshness,
            "machine_or_volume_changed": False,
            "passed": True,
        }
        return report

    def retained_build_ids(self, route: dict | None = None) -> set[str]:
        if route is None:
            route, _ = self.route()
        keep = set()
        validate_route_record(route)
        for entry in [route_target(route), route.get("previous"), route.get("native_rollback")]:
            if entry is None:
                continue
            route_target({"version": 1, "current": entry})
            source = entry["build_id"]
            if target_engine(entry) == "tantivy":
                index = self.healthy(entry, require_fresh=False)["index"]
                source = index.get("source_build_id")
                manifest = index.get("source_manifest") or {}
                if (
                    not isinstance(source, str)
                    or not re.fullmatch(BUILD_PATTERN, source)
                    or manifest.get("build_id") != source
                    or type(index.get("snapshot_watermark")) is not int
                    or manifest.get("source_watermark") != index["snapshot_watermark"]
                    or index.get("full_source") is not True
                    or index.get("import_complete") is not True
                ):
                    raise RuntimeError("Tantivy source identity unknown; refusing artifact cleanup")
            twin = entry.get("legacy_build_id", source.removesuffix("-px05"))
            if not isinstance(twin, str) or not re.fullmatch(BUILD_PATTERN, twin):
                raise RuntimeError("Legacy source identity unknown; refusing artifact cleanup")
            keep.update([entry["build_id"], source, source.removesuffix("-px05"), twin])
        return keep

    def prune_artifacts(self):
        route, etag = self.route()
        keep = self.retained_build_ids(route)
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


def require_automatic_refresh_supported(logger):
    service = Refresh(logger)
    try:
        route, _ = service.route()
        require_native_refresh(route_target(route))
    finally:
        service.close()


def protected_source_build_ids(logger) -> set[str]:
    service = Refresh(logger)
    try:
        return service.retained_build_ids()
    finally:
        service.close()


@task(retries=3, retry_delay_seconds=[2, 5, 10], retry_jitter_factor=1)
def refresh_snapshot(generation: Path | None = None):
    from prefect import get_run_logger

    refresh = Refresh(get_run_logger())
    try:
        expected = (
            None if generation is None else json.loads((generation / "manifest.json").read_bytes())
        )
        report = refresh.resume_published(expected)
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


def rollback_snapshot():
    from prefect import get_run_logger

    service = Refresh(get_run_logger())
    try:
        report = service.rollback_to_native()
        output = (
            Path(os.environ["INDEXER_HOME"])
            / "refresh-receipts"
            / ("rollback-" + str(int(time.time())) + ".json")
        )
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2) + "\n")
        return report
    finally:
        service.close()
