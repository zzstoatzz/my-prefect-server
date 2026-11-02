from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path

import boto3
import httpx
from botocore.exceptions import ClientError
from prefect import task

SLOTS = ("typeahead-startup-20261024", "typeahead-search-refresh")
ROUTE_KEY = "control/search-routing.json"
ROUTER = "https://typeahead-search-transport.n8-3e9.workers.dev"
PUBLIC = "https://typeahead.waow.tech"
OWNER = "typeahead-snapshot-refresh"
TANTIVY_ORIGIN = "https://typeahead-tantivy-dev.fly.dev"
TANTIVY_SLOTS = ("typeahead-tantivy-a", "typeahead-tantivy-b")
TANTIVY_FORMAT = "tantivy-0.27-f32-did-expiry-v4"
BUILD_PATTERN = r"b[0-9]{10}-[A-Za-z0-9_-]+"


class BackendNotReady(RuntimeError):
    """A backend's /health reports it not ready or past its sync-lag bound.

    A serving slot's readiness follows its poll lag, so this can clear on the
    next read; callers that wait can retry it.
    """


class FlyAPIError(RuntimeError):
    def __init__(self, method, app, path, status_code):
        self.status_code = status_code
        super().__init__(f"Fly {method} {app}/{path} failed: HTTP {status_code}")


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
        target.get("origin")
        not in (TANTIVY_ORIGIN, *(f"https://{app}.fly.dev" for app in TANTIVY_SLOTS))
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
    if route.get("mutation") is not None:
        from mps.typeahead_tantivy_slots import validate_mutation

        validate_mutation(route["mutation"])


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
    def execution_identity(self):
        from prefect.runtime import flow_run

        if not flow_run.id:
            raise RuntimeError("Slot lifecycle must run in the existing cloud publisher flow")
        return str(flow_run.id)

    def execution_finished(self, execution_id):
        from uuid import UUID

        from prefect.client.orchestration import get_client

        with get_client(sync_client=True) as client:
            run = client.read_flow_run(UUID(execution_id))
        return run.state is not None and run.state.is_final()

    def _fly_url(self, app, path):
        return f"https://api.machines.dev/v1/apps/{app}/{path}"

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

    def close(self):
        self.http.close()
        self.s3.close()

    def fly(self, method: str, app: str, path: str, body=None):
        tokens = {
            TANTIVY_SLOTS[0]: "FLY_TANTIVY_A_TOKEN",
            TANTIVY_SLOTS[1]: "FLY_TANTIVY_B_TOKEN",
        }
        if app not in tokens:
            raise ValueError("Unmanaged provider app")
        result = self.http.request(
            method,
            self._fly_url(app, path),
            headers={"Authorization": "Bearer " + os.environ[tokens[app]]},
            json=body,
        )
        if result.status_code >= 400:
            raise FlyAPIError(method, app, path, result.status_code)
        if len(result.content) > 2 * 1024 * 1024:
            raise RuntimeError("Provider metadata response exceeds its byte budget")
        return result.json() if result.content else None

    def route(self):
        result = self.s3.get_object(Bucket=self.bucket, Key=ROUTE_KEY)
        raw = result["Body"].read(16385)
        if len(raw) > 16384:
            raise ValueError("Routing record too large")
        route = json.loads(raw)
        validate_route_record(route)
        return route, result["ETag"]

    def _put_route(self, route: dict, etag: str, *, release_mutation=None):
        before, actual_etag = self.route()
        if actual_etag != etag:
            raise RuntimeError("Routing changed before conditional update")
        lease = before.get("mutation")
        if lease is not None:
            if route["current"]["origin"] == f"https://{lease['app']}.fly.dev":
                raise RuntimeError("Routing target is leased for inactive mutation")
            if release_mutation is None:
                route["mutation"] = lease
            elif (
                release_mutation != lease
                or {key: value for key, value in before.items() if key != "mutation"} != route
            ):
                raise RuntimeError("Mutation release changed routing identity")
        elif release_mutation is not None:
            raise RuntimeError("Mutation lease disappeared before release")
        elif route.get("mutation") is not None and (
            {key: value for key, value in route.items() if key != "mutation"} != before
            or route["current"]["origin"] == f"https://{route['mutation']['app']}.fly.dev"
        ):
            raise RuntimeError("Mutation acquisition cannot change the serving route")
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
            raise BackendNotReady("Backend is not fresh and ready")
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

    def retained_build_ids(self, route: dict | None = None) -> set[str]:
        """Tantivy generations that routing or a pending candidate still needs."""
        if route is None:
            route, _ = self.route()
        validate_route_record(route)
        if target_engine(route["current"]) != "tantivy":
            raise RuntimeError("Only a Tantivy route can authorize artifact cleanup")
        keep = set()
        for entry in (route["current"], route.get("previous")):
            if entry is None or target_engine(entry) != "tantivy":
                continue
            if self.tantivy_publication(entry) is None:
                raise RuntimeError(
                    "Routed Tantivy publication is missing; refusing artifact cleanup"
                )
            keep.add(entry["build_id"])
        for app in TANTIVY_SLOTS:
            pending = self.optional_metadata(f"control/tantivy-slots/{app}/pending.json", 4096)
            if pending is None:
                continue
            if (
                pending.get("schema_version") != 1
                or pending.get("owner") != OWNER
                or pending.get("app") != app
            ):
                raise ValueError("Invalid pending candidate retention record")
            builds = [
                pending.get(field) for field in ("build_id", "source_build_id", "legacy_build_id")
            ]
            if any(
                not isinstance(value, str) or not re.fullmatch(BUILD_PATTERN, value)
                for value in builds
            ):
                raise ValueError("Invalid pending candidate source identity")
            key = f"releases/search-tantivy/{pending['build_id']}/manifest.json"
            result = self.s3.get_object(Bucket=self.bucket, Key=key)
            try:
                raw = result["Body"].read(1024 * 1024 + 1)
            finally:
                result["Body"].close()
            if len(raw) > 1024 * 1024 or hashlib.sha256(raw).hexdigest() != pending.get(
                "artifact_manifest_sha256"
            ):
                raise ValueError("Pending artifact manifest identity changed")
            manifest = json.loads(raw)
            self.tantivy_adapter().validate_source(manifest, self.tantivy_source_manifest(manifest))
            if [
                manifest[field] for field in ("active_build", "source_build_id", "legacy_build_id")
            ] != builds:
                raise ValueError("Pending artifact source linkage changed")
            keep.add(manifest["active_build"])
        return keep

    def optional_metadata(self, key, limit):

        try:
            result = self.s3.get_object(Bucket=self.bucket, Key=key)
        except FileNotFoundError:
            return None
        except ClientError as error:
            if error.response["Error"]["Code"] in ("NoSuchKey", "404"):
                return None
            raise
        try:
            raw = result["Body"].read(limit + 1)
        finally:
            result["Body"].close()
        if len(raw) > limit:
            raise ValueError("Managed metadata exceeds its byte budget")
        return json.loads(raw)

    def tantivy_adapter(self):
        from mps.typeahead_tantivy import TantivyAdapter

        return TantivyAdapter(
            Path(os.environ["INDEX_TANTIVY_ADAPTER_PATH"]),
            os.environ["INDEX_TANTIVY_ADAPTER_SHA256"],
        )

    def tantivy_publication(self, target: dict) -> dict | None:

        key = f"releases/search-tantivy/{target['build_id']}/manifest.json"
        try:
            result = self.s3.get_object(Bucket=self.bucket, Key=key)
        except FileNotFoundError:
            return None
        except ClientError as exc:
            if exc.response["Error"]["Code"] in ("NoSuchKey", "404"):
                return None
            raise
        raw = result["Body"].read(32 * 1024 * 1024 + 1)
        if len(raw) > 32 * 1024 * 1024:
            raise ValueError("Tantivy publication manifest too large")
        manifest = json.loads(raw)
        adapter = self.tantivy_adapter()
        adapter.validate_manifest(manifest)
        if manifest["active_build"] != target["build_id"] or any(
            target[key] != manifest[field]
            for key, field in (
                ("format", "backend_format"),
                ("binary_sha256", "binary_sha256"),
                ("retrieval_strategy", "retrieval_strategy"),
            )
        ):
            raise ValueError("Routed Tantivy publication identity mismatch")
        source = self.tantivy_source_manifest(manifest)
        adapter.validate_source(manifest, source)
        return manifest

    def tantivy_source_binding(self, manifest: dict) -> tuple[str, str | None]:
        if manifest.get("source_kind") == "sqlite-committed-prefix":
            anchor = manifest["rollback_native_anchor"]
            return anchor["build_id"], anchor["manifest_sha256"]
        return manifest["source_build_id"], manifest.get("source_manifest_sha256")

    def tantivy_source_manifest(self, manifest: dict) -> bytes:
        self.tantivy_adapter().validate_manifest(manifest)
        native_build, _ = self.tantivy_source_binding(manifest)
        body = self.s3.get_object(
            Bucket=self.bucket,
            Key=f"releases/search/{native_build}/manifest.json",
        )["Body"]
        try:
            raw = body.read(1024 * 1024 + 1)
        finally:
            body.close()
        if len(raw) > 1024 * 1024:
            raise ValueError("Native rollback manifest is too large")
        self.tantivy_adapter().validate_source(manifest, raw)
        return raw

    def install_tantivy_bundle(
        self, bundle: Path, destination: Path, manifest: dict, binary: Path
    ) -> dict:
        return self.tantivy_adapter().install(
            bundle, destination, manifest, binary, self.tantivy_source_manifest(manifest)
        )

    def freeze_tantivy_bundle(
        self, data: Path, destination: Path, manifest: dict, binary: Path
    ) -> dict:
        return self.tantivy_adapter().freeze(
            data, destination, manifest, binary, self.tantivy_source_manifest(manifest)
        )

    def qualify_tantivy_candidate(
        self, url: str, manifest: dict, installation: dict, provider_attestation: dict
    ) -> dict:
        return self.tantivy_adapter().qualify(
            url,
            manifest,
            installation,
            provider_attestation,
            self.tantivy_source_manifest(manifest),
        )

    def prune_artifacts(self):
        route, etag = self.route()
        if route.get("mutation") is not None:
            raise RuntimeError("Inactive mutation is pending; artifact cleanup is fenced")
        keep = self.retained_build_ids(route)
        victims = []
        for page in self.s3.get_paginator("list_objects_v2").paginate(
            Bucket=self.bucket, Prefix="releases/search-tantivy/"
        ):
            for item in page.get("Contents", []):
                parts = item["Key"].split("/")
                if (
                    len(parts) == 4
                    and re.fullmatch(BUILD_PATTERN, parts[2])
                    and parts[2] not in keep
                    and parts[3] in ("index.tar", "manifest.json")
                ):
                    victims.append(item)
        for start in range(0, len(victims), 1000):
            current, actual_etag = self.route()
            if actual_etag != etag or current != route or self.retained_build_ids(route) != keep:
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


def prune_snapshot_artifacts():
    from prefect import get_run_logger

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
