"""Owned inactive-slot lifecycle and explicit evidence-gated routing transactions."""

from __future__ import annotations

import hashlib
import json
import re
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx
from botocore.exceptions import ClientError

from mps.typeahead_tantivy import durable_receipt

OWNER = "typeahead-snapshot-refresh"
APPS = ("typeahead-tantivy-a", "typeahead-tantivy-b")
GUEST = {"cpu_kind": "shared", "cpus": 2, "memory_mb": 1024}
DIGEST = re.compile(r"[a-f0-9]{64}")
BUILD = re.compile(r"b[0-9]{10}-[A-Za-z0-9_-]+")
GATES = (
    "matched_performance",
    "full_functional",
    "live_source_coverage",
    "worker_http_smoke",
    "recovery",
    "refresh_lifecycle",
)


def raw_json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode() + b"\n"


def validate_mutation(value):
    if (
        not isinstance(value, dict)
        or set(value) != {"owner", "request_id", "app", "acquired_at"}
        or value.get("owner") != OWNER
        or value.get("app") not in APPS
        or not isinstance(value.get("request_id"), str)
        or not re.fullmatch(r"[A-Za-z0-9_-]{8,80}", value["request_id"])
        or type(value.get("acquired_at")) is not int
        or value["acquired_at"] < 0
    ):
        raise ValueError("Invalid inactive mutation lease")
    return value


def validate_release_proof(raw, expected_sha256, binding):
    if (
        not isinstance(expected_sha256, str)
        or not DIGEST.fullmatch(expected_sha256)
        or hashlib.sha256(raw).hexdigest() != expected_sha256
    ):
        raise ValueError("Empirical release proof checksum mismatch")
    proof = json.loads(raw)
    if (
        proof.get("schema_version") != 1
        or proof.get("engine") != "tantivy"
        or proof.get("test_only") is not False
        or proof.get("passed") is not True
        or proof.get("binding") != binding
    ):
        raise ValueError("A complete non-test empirical release proof is required")
    gates = proof.get("gates")
    if (
        not isinstance(gates, dict)
        or set(gates) != set(GATES)
        or any(
            not isinstance(gates[name], dict)
            or gates[name].get("passed") is not True
            or not isinstance(gates[name].get("evidence_sha256"), str)
            or not DIGEST.fullmatch(gates[name]["evidence_sha256"])
            for name in GATES
        )
    ):
        raise ValueError("Empirical release proof is missing required evidence gates")
    return proof


class Slots:
    def __init__(self, service, journal_root: Path, *, timeout_seconds=2400):
        if (
            not 1 <= timeout_seconds <= 7200
            or journal_root.is_symlink()
            or journal_root.absolute() != journal_root.resolve()
        ):
            raise ValueError("Invalid bounded slot preparation settings")
        self.service = service
        self.root = journal_root
        self.root.mkdir(parents=True, exist_ok=True)
        self.timeout = timeout_seconds
        self.etags = {}

    def journal(self, request_id):
        key = f"control/tantivy-operations/{request_id}.json"
        try:
            result = self.service.s3.get_object(Bucket=self.service.bucket, Key=key)
        except FileNotFoundError:
            return None
        except ClientError as exc:
            if exc.response["Error"]["Code"] in ("NoSuchKey", "404"):
                return None
            raise
        stream = result["Body"]
        try:
            raw = stream.read(65537)
        finally:
            stream.close()
        if len(raw) > 65536:
            raise ValueError("Slot operation journal exceeds its byte budget")
        self.etags[request_id] = result["ETag"]
        state = json.loads(raw)
        if (
            state.get("request_id") != request_id
            or state.get("owner") != OWNER
            or type(state.get("journal_revision")) is not int
        ):
            raise ValueError("Invalid durable slot operation identity")
        previous = state.get("execution_id")
        execution = self.service.execution_identity()
        if previous != execution and not self.service.execution_finished(previous):
            raise RuntimeError("Earlier publisher execution is still active or unconfirmed")
        state["execution_id"] = execution
        return state

    def read(self, key, maximum=32 * 1024 * 1024):
        if (
            not isinstance(key, str)
            or not re.fullmatch(r"[A-Za-z0-9/_.-]{1,256}", key)
            or ".." in key.split("/")
        ):
            raise ValueError("Invalid managed object key")
        try:
            result = self.service.s3.get_object(Bucket=self.service.bucket, Key=key)
        except FileNotFoundError:
            return None
        except ClientError as exc:
            if exc.response["Error"]["Code"] in ("NoSuchKey", "404"):
                return None
            raise
        stream = result["Body"]
        try:
            raw = stream.read(maximum + 1)
        finally:
            stream.close()
        if len(raw) > maximum:
            raise ValueError("Managed metadata exceeds its byte budget")
        return raw

    def immutable(self, key, raw):
        before = self.read(key)
        if before is not None:
            if before != raw:
                raise RuntimeError("Immutable slot metadata changed")
            return
        self.service.s3.put_object(
            Bucket=self.service.bucket,
            Key=key,
            Body=raw,
            ContentType="application/json",
            IfNoneMatch="*",
        )

    def artifact(self, build_id):
        if not isinstance(build_id, str) or not BUILD.fullmatch(build_id):
            raise ValueError("Invalid Tantivy generation")
        raw = self.read(f"releases/search-tantivy/{build_id}/manifest.json")
        if raw is None:
            raise ValueError("Tantivy generation has not been published")
        manifest = json.loads(raw)
        self.service.tantivy_adapter().validate_manifest(manifest)
        if manifest["active_build"] != build_id or any(
            field in manifest
            for field in (
                "provider_app",
                "image_digest",
                "guest",
                "volume_id",
                "volume_size_gib",
                "public_service_ports",
            )
        ):
            raise ValueError("A portable immutable Tantivy publication is required")
        source = self.service.tantivy_source_manifest(manifest)
        self.service.tantivy_adapter().validate_source(manifest, source)
        return manifest, raw

    def save(self, state, phase=None):
        if phase is not None:
            state["phase"] = phase
        state["updated_at"] = int(time.time())
        request_id = state["request_id"]
        state["journal_revision"] = state.get("journal_revision", 0) + 1
        raw = raw_json(state)
        key = f"control/tantivy-operations/{request_id}.json"
        conditions = (
            {"IfMatch": self.etags[request_id]}
            if request_id in self.etags
            else {"IfNoneMatch": "*"}
        )
        try:
            result = self.service.s3.put_object(
                Bucket=self.service.bucket,
                Key=key,
                Body=raw,
                ContentType="application/json",
                **conditions,
            )
            self.etags[request_id] = result["ETag"]
        except Exception:
            observed = self.service.s3.get_object(Bucket=self.service.bucket, Key=key)
            try:
                if observed["Body"].read(65537) != raw:
                    raise RuntimeError("Slot journal conditional write is unconfirmed") from None
            finally:
                observed["Body"].close()
            self.etags[request_id] = observed["ETag"]
        try:
            durable_receipt(self.root / (request_id + ".json"), state)
        except OSError:
            self.service.logger.warning("Slot journal persisted remotely; local cache unavailable")

    def guard(self, state, *, fresh=False):
        if state.get("app") not in APPS:
            raise ValueError("Only approved inactive Tantivy apps may be mutated")
        route, etag = self.service.route()
        lease = route.get("mutation")
        expected = state["route"]
        if {
            key: value for key, value in route.items() if key != "mutation"
        } != expected or lease != state.get("mutation"):
            raise RuntimeError("Routing changed; same preparation intent must be reconciled")
        if route["current"]["origin"] == f"https://{state['app']}.fly.dev":
            raise RuntimeError("Refusing mutation of the serving app")
        self.service.healthy(route["current"], require_fresh=fresh)
        code, public, _ = self.service.get("https://typeahead.waow.tech", "/health/freshness")
        if (
            code not in (200, 503)
            or public.get("ok") is not True
            or public.get("probe_ok") is not True
            or public.get("build_id") != route["current"]["build_id"]
        ):
            raise RuntimeError("Public serving identity is unavailable or changed")
        return route, etag

    def acquire(self, state):
        route, etag = self.service.route()
        before = {key: value for key, value in route.items() if key != "mutation"}
        if before != state["route"]:
            raise RuntimeError("Routing changed before preparation lease")
        lease = route.get("mutation")
        if lease is not None and (
            validate_mutation(lease)["request_id"] != state["request_id"]
            or lease["app"] != state["app"]
        ):
            raise RuntimeError("Another inactive mutation is pending")
        state["mutation"] = lease or {
            "owner": OWNER,
            "request_id": state["request_id"],
            "app": state["app"],
            "acquired_at": int(time.time()),
        }
        self.save(state, "lease-intent")
        if lease is None:
            self.service._put_route({**route, "mutation": state["mutation"]}, etag)
        self.guard(state)

    def protect(self, state):
        key = f"control/tantivy-slots/{state['app']}/pending.json"
        value = {
            "schema_version": 1,
            "owner": OWNER,
            "request_id": state["request_id"],
            "app": state["app"],
            "build_id": state["build_id"],
            "source_build_id": state["source_build_id"],
            "legacy_build_id": state["legacy_build_id"],
            "artifact_manifest_sha256": state["artifact_manifest_sha256"],
        }
        before = self.read(key, 4096)
        if before is None:
            self.immutable(key, raw_json(value))
        elif json.loads(before) != value:
            raise RuntimeError("A different prepared candidate owns this slot")

    def unprotect(self, state):
        key = f"control/tantivy-slots/{state['app']}/pending.json"
        if self.read(key, 4096) is None:
            return
        result = self.service.s3.get_object(Bucket=self.service.bucket, Key=key)
        try:
            current = json.loads(result["Body"].read(4097))
        finally:
            result["Body"].close()
        if current.get("request_id") != state["request_id"]:
            raise RuntimeError("Candidate retention ownership changed")
        self.service.s3.delete_object(Bucket=self.service.bucket, Key=key, IfMatch=result["ETag"])

    def release(self, state):
        route, etag = self.service.route()
        if route.get("mutation") != state.get("mutation"):
            raise RuntimeError("Mutation lease changed; refusing release")
        self.service._put_route(
            {key: value for key, value in route.items() if key != "mutation"},
            etag,
            release_mutation=state["mutation"],
        )
        self.save(state)

    def action(self, state, name, method, path, body=None):
        if state.get("pending_action") is not None:
            raise RuntimeError(
                "An earlier provider action must be reconciled before another mutation"
            )
        self.guard(state)
        state["pending_action"] = {"name": name, "method": method, "path": path}
        self.save(state, name + "-intent")
        try:
            return self.service.fly(method, state["app"], path, body)
        except (httpx.TransportError, RuntimeError):
            return None

    def confirm(self, state, phase):
        state["pending_action"] = None
        self.save(state, phase)

    def machine(self, state, mid):
        value = self.service.fly("GET", state["app"], "machines/" + mid)
        config = value.get("config", {})
        metadata = config.get("metadata", {})
        if (
            value.get("id") != mid
            or metadata.get("owner") != OWNER
            or metadata.get("role") != "tantivy-slot"
            or metadata.get("request_id") != state["request_id"]
            or metadata.get("deployment_sha256") != state["deployment_sha256"]
            or config.get("image") != state["image_digest"]
            or config.get("mounts") != [{"volume": state["volume_id"], "path": "/data"}]
            or any(config.get("guest", {}).get(key) != expected for key, expected in GUEST.items())
            or config.get("env") != state["config"]["env"]
            or config.get("init") != state["config"]["init"]
        ):
            raise RuntimeError("Owned Tantivy candidate configuration changed")
        if mid in self.service.tantivy_adapter().installer.PROTECTED_MACHINES:
            raise RuntimeError("Protected machine cannot be a Tantivy slot candidate")
        return value

    def wait(self, state, wanted):
        deadline = time.monotonic() + min(180, self.timeout)
        while True:
            machine = self.machine(state, state["machine_id"])
            if machine.get("state") == wanted:
                return machine
            if time.monotonic() >= deadline:
                raise RuntimeError("Candidate state transition remains unconfirmed")
            time.sleep(0.5)

    def start(self, state):
        pending = state.get("pending_action")
        if pending is not None:
            if pending["name"] not in ("start", "stop"):
                raise RuntimeError("Candidate creation must be reconciled before start")
            self.wait(state, "started" if pending["name"] == "start" else "stopped")
            self.confirm(state, "reconciled-" + pending["name"])
        before = self.machine(state, state["machine_id"])
        if before["state"] != "started":
            if before["state"] not in ("stopped", "created"):
                raise RuntimeError("Candidate is not safely startable")
            self.action(state, "start", "POST", "machines/" + state["machine_id"] + "/start")
        machine = self.wait(state, "started")
        self.confirm(state, "started")
        return machine

    def stop(self, state):
        pending = state.get("pending_action")
        if pending is not None:
            if pending["name"] not in ("start", "stop"):
                raise RuntimeError("Candidate creation must be reconciled before cancellation")
            self.wait(state, "started" if pending["name"] == "start" else "stopped")
            self.confirm(state, "reconciled-" + pending["name"])
        before = self.machine(state, state["machine_id"])
        if before["state"] != "stopped":
            self.action(
                state,
                "stop",
                "POST",
                "machines/" + state["machine_id"] + "/stop",
                {"signal": "SIGTERM", "timeout": 60},
            )
        self.wait(state, "stopped")
        self.confirm(state, "stopped")

    def installation(self, state):
        code = "import os,sys;f=os.open('/data/tantivy/installation.json',os.O_RDONLY|os.O_NOFOLLOW);d=os.read(f,65537);os.close(f);assert len(d)<=65536;sys.stdout.buffer.write(d)"
        result = self.service.fly(
            "POST",
            state["app"],
            "machines/" + state["machine_id"] + "/exec",
            {"command": ["python3", "-c", code], "timeout": 10},
        )
        if (
            not isinstance(result, dict)
            or result.get("exit_code") != 0
            or result.get("exit_signal", 0) != 0
            or not isinstance(result.get("stdout"), str)
            or len(result["stdout"].encode()) > 65536
        ):
            raise RuntimeError("Candidate durable installation receipt is unavailable")
        receipt = json.loads(result["stdout"])
        if (
            receipt.get("deployment_sha256") != state["deployment_sha256"]
            or receipt.get("artifact_manifest_sha256") != state["artifact_manifest_sha256"]
        ):
            raise RuntimeError("Candidate installation deployment identity differs")
        return receipt

    def qualify(self, state, bound):
        machine = self.machine(state, state["machine_id"])
        volume = self.service.fly("GET", state["app"], "volumes/" + state["volume_id"])
        if (
            volume.get("id") != state["volume_id"]
            or volume.get("size_gb") != 20
            or volume.get("encrypted") is not True
            or machine.get("state") != "started"
        ):
            raise RuntimeError("Candidate final provider shape differs")
        ports = sorted(
            {
                port["port"]
                for service in machine["config"].get("services", [])
                for port in service.get("ports", [])
            }
        )
        if (
            ports != [80, 443]
            or len(machine["config"].get("services", [])) != 1
            or machine["config"]["services"][0].get("internal_port") != 8080
        ):
            raise RuntimeError("Candidate provider ingress differs")
        provider = {
            "schema": 1,
            "provider": "fly",
            "observed_at": datetime.fromtimestamp(int(time.time()), UTC)
            .isoformat()
            .replace("+00:00", "Z"),
            "app": state["app"],
            "machine_id": state["machine_id"],
            "instance_id": machine.get("instance_id"),
            "state": "started",
            "image_digest": machine["config"]["image"],
            "guest": GUEST,
            "mounts": [
                {
                    "volume_id": state["volume_id"],
                    "path": "/data",
                    "size_gib": 20,
                    "encrypted": True,
                }
            ],
            "owner": OWNER,
            "role": "tantivy-slot",
            "public_service_ports": ports,
            "provider_config_reserved_env_overrides": any(
                key.startswith("FLY_") for key in machine["config"].get("env", {})
            ),
            "test_only": False,
        }
        receipt = self.installation(state)
        qualified = self.service.qualify_tantivy_candidate(
            f"https://{state['app']}.fly.dev", bound, receipt, provider
        )
        return qualified, provider

    def wait_qualified(self, state, bound):
        deadline = time.monotonic() + self.timeout
        while True:
            self.guard(state)
            try:
                return self.qualify(state, bound)
            except (httpx.HTTPError, RuntimeError, ValueError, KeyError):
                if time.monotonic() >= deadline:
                    raise RuntimeError(
                        "Inactive candidate runtime qualification timed out"
                    ) from None
                time.sleep(0.5)

    def config(self, state, bound, image):
        key = f"control/tantivy-slots/{state['app']}/{state['request_id']}.json"
        raw = raw_json(bound)
        digest = hashlib.sha256(raw).hexdigest()
        self.immutable(key, raw)
        state.update(deployment_key=key, deployment_sha256=digest)
        return {
            "image": image,
            "init": {"cmd": ["python3", "/app/tantivy-slot.py"]},
            "env": {
                "TANTIVY_DEPLOYMENT_KEY": key,
                "EXPECTED_DEPLOYMENT_SHA256": digest,
                "EXPECTED_BUILD_ID": state["build_id"],
            },
            "guest": dict(GUEST),
            "mounts": [{"volume": state["volume_id"], "path": "/data"}],
            "auto_destroy": False,
            "restart": {"policy": "on-failure", "max_retries": 3},
            "stop_config": {"signal": "SIGTERM", "timeout": 60000000000},
            "metadata": {
                "owner": OWNER,
                "role": "tantivy-slot",
                "request_id": state["request_id"],
                "build_id": state["build_id"],
                "deployment_sha256": digest,
            },
            "services": [
                {
                    "protocol": "tcp",
                    "internal_port": 8080,
                    "ports": [
                        {"port": 80, "handlers": ["http"]},
                        {"port": 443, "handlers": ["tls", "http"]},
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
                    "grace_period": "60s",
                }
            },
        }

    def allocate(self, state, manifest):
        pending = state.get("pending_action")
        machines = [
            item
            for item in self.service.fly("GET", state["app"], "machines")
            if item.get("state") != "destroyed"
        ]
        volumes = self.service.fly("GET", state["app"], "volumes")
        retired = state.get("retired_previous")
        if retired is not None and state.get("retirement_progress", {}).get("retire-volume"):
            volumes = [
                item
                for item in volumes
                if not (
                    item.get("id") == retired["volume_id"]
                    and item.get("state")
                    in (
                        "destroyed",
                        "deleted",
                        "pending_destroy",
                        "scheduling_destroy",
                        "fork_cleanup",
                        "waiting_for_detach",
                        "destroying",
                    )
                    and not item.get("attached_machine_id")
                    and not item.get("attached_alloc_id")
                )
            ]
        candidate = [item for item in machines if item.get("name") == state["machine_name"]]
        if len(candidate) > 1 or any(item not in candidate for item in machines):
            raise RuntimeError("Inactive app has an unrelated or retained machine")
        if pending is not None and pending["name"] == "create-machine" and not candidate:
            raise RuntimeError(
                "Prior machine creation remains unconfirmed; refusing duplicate creation"
            )
        matches = [item for item in volumes if item.get("name") == state["volume_name"]]
        if len(matches) > 1 or any(item not in matches for item in volumes):
            raise RuntimeError("Inactive app has an unrelated or retained volume")
        if not matches:
            if state.get("pending_action") is not None:
                raise RuntimeError(
                    "Pending provider action cannot be retried without reconciliation"
                )
            created = self.action(
                state,
                "create-volume",
                "POST",
                "volumes",
                {
                    "name": state["volume_name"],
                    "region": state["region"],
                    "size_gb": 20,
                    "encrypted": True,
                    "auto_backup_enabled": False,
                    "compute": GUEST,
                },
            )
            if isinstance(created, dict):
                matches = [created]
            else:
                matches = [
                    item
                    for item in self.service.fly("GET", state["app"], "volumes")
                    if item.get("name") == state["volume_name"]
                ]
        if (
            len(matches) != 1
            or matches[0].get("name") != state["volume_name"]
            or not re.fullmatch(r"vol_[A-Za-z0-9]+", matches[0].get("id", ""))
            or matches[0].get("size_gb") != 20
            or matches[0].get("encrypted") is not True
        ):
            raise RuntimeError("Volume creation remains unconfirmed; resume only this request")
        state["volume_id"] = matches[0]["id"]
        if pending is None or pending["name"] == "create-volume":
            self.confirm(state, "volume-ready")
        else:
            self.save(state, "volume-ready")
        bound = {
            **manifest,
            "provider_app": state["app"],
            "image_digest": state["image_digest"],
            "guest": dict(GUEST),
            "volume_id": state["volume_id"],
            "volume_size_gib": 20,
            "public_service_ports": [80, 443],
            "deployment_schema_version": 1,
            "request_id": state["request_id"],
            "artifact_manifest_key": f"releases/search-tantivy/{state['build_id']}/manifest.json",
            "artifact_manifest_sha256": state["artifact_manifest_sha256"],
            "source_manifest_key": f"releases/search/{self.service.tantivy_source_binding(manifest)[0]}/manifest.json",
        }
        state["config"] = self.config(state, bound, state["image_digest"])
        self.save(state, "deployment-ready")
        if not candidate:
            result = self.action(
                state,
                "create-machine",
                "POST",
                "machines",
                {
                    "name": state["machine_name"],
                    "region": state["region"],
                    "config": state["config"],
                    "skip_launch": True,
                },
            )
            if isinstance(result, dict):
                candidate = [result]
            else:
                candidate = [
                    item
                    for item in self.service.fly("GET", state["app"], "machines")
                    if item.get("name") == state["machine_name"]
                ]
        if len(candidate) != 1 or not re.fullmatch(r"[a-f0-9]{14}", candidate[0].get("id", "")):
            raise RuntimeError("Machine creation remains unconfirmed; resume only this request")
        state["machine_id"] = candidate[0]["id"]
        self.machine(state, state["machine_id"])
        if (
            state.get("pending_action") is None
            or state["pending_action"]["name"] == "create-machine"
        ):
            self.confirm(state, "machine-ready")
        else:
            self.save(state, "machine-ready")
        return bound

    def admission_record(self, target):
        digest = target.get("admission_sha256")
        if not isinstance(digest, str) or not DIGEST.fullmatch(digest):
            raise ValueError("A bound Tantivy admission identity is required")
        raw = self.read(f"control/tantivy-admissions/{digest}.json", 65536)
        if raw is None:
            raise ValueError("Tantivy admission resource enrollment is missing")
        record = json.loads(raw)
        if (
            record.get("schema_version") != 1
            or self.service.tantivy_adapter().installer.digest_json(record.get("admission"))
            != digest
        ):
            raise ValueError("Tantivy admission enrollment checksum differs")
        deployment = self.read(record["deployment_key"])
        if deployment is None or hashlib.sha256(deployment).hexdigest() != record.get(
            "deployment_sha256"
        ):
            raise ValueError("Enrolled deployment identity differs")
        bound = json.loads(deployment)
        if (
            bound.get("active_build") != target["build_id"]
            or f"https://{bound.get('provider_app')}.fly.dev" != target["origin"]
        ):
            raise ValueError("Admission enrollment does not name this route")
        return record, bound

    def release_binding(self, target):
        record, bound = self.admission_record(target)
        app = bound["provider_app"]
        if app not in APPS:
            raise ValueError("Only an enrolled serving slot can authorize previous-slot reuse")
        machine = self.service.fly("GET", app, "machines/" + record["machine_id"])
        config = machine.get("config", {})
        if (
            config.get("image") != bound["image_digest"]
            or config.get("mounts") != [{"volume": bound["volume_id"], "path": "/data"}]
            or config.get("metadata", {}).get("owner") != OWNER
            or config.get("metadata", {}).get("role") != "tantivy-slot"
            or config.get("env", {}).get("EXPECTED_DEPLOYMENT_SHA256")
            != record["deployment_sha256"]
        ):
            raise RuntimeError("Serving slot provider enrollment changed")
        return {
            "binary_sha256": bound["binary_sha256"],
            "retrieval_strategy": bound["retrieval_strategy"],
            "canonical_rules_source_sha256": bound["canonical_rules_source_sha256"],
            "image_digest": bound["image_digest"],
            "guest": GUEST,
            "volume_size_gib": 20,
        }

    def require_release(self, target, key, digest):
        if not key or not digest:
            raise RuntimeError(
                "Actual empirical release proof is required; no production proof is configured"
            )
        raw = self.read(key, 1024 * 1024)
        if raw is None:
            raise ValueError("Immutable empirical release proof is missing")
        proof = validate_release_proof(raw, digest, self.release_binding(target))
        return {"key": key, "sha256": digest, "passed": proof["passed"]}

    def retained(self, target):
        manifest, raw = self.artifact(target["build_id"])
        record, bound = self.admission_record(target)
        if (
            record["admission"].get("bundle_sha256", manifest["sha256"]) != manifest["sha256"]
            or bound.get("sha256") != manifest["sha256"]
        ):
            raise ValueError("Retained artifact differs from installed admission")
        source = json.loads(self.service.tantivy_source_manifest(manifest))
        native_build, native_manifest_sha = self.service.tantivy_source_binding(manifest)
        native_watermark = source["source_watermark"]
        twin_ref = source.get("legacy_twin", {}).get("manifest", {})
        twin_raw = self.read(twin_ref.get("object_key"), 1024 * 1024)
        if (
            twin_raw is None
            or hashlib.sha256(twin_raw).hexdigest() != twin_ref.get("sha256")
            or len(twin_raw) != twin_ref.get("byte_size")
        ):
            raise ValueError("Native rollback twin manifest is not retained exactly")
        twin = json.loads(twin_raw)
        if (
            twin.get("active_build") != manifest["legacy_build_id"]
            or twin.get("source_watermark") != native_watermark
        ):
            raise ValueError("Native rollback twin identity differs")
        objects = [
            (manifest["object_key"], manifest["byte_size"]),
            (source["snapshot_url"], source["byte_size"]),
            (source["prefix_map"]["object_key"], source["prefix_map"]["byte_size"]),
            (twin["snapshot_url"], twin["byte_size"]),
        ]
        witnesses = []
        for key, size in objects:
            result = self.service.s3.head_object(Bucket=self.service.bucket, Key=key)
            if result.get("ContentLength") != size:
                raise ValueError("Referenced rollback artifact is missing or changed size")
            witnesses.append({"key": key, "byte_size": size, "etag": result.get("ETag")})
        return {
            "artifact_manifest_sha256": hashlib.sha256(raw).hexdigest(),
            "source_manifest_sha256": manifest["source_manifest_sha256"],
            "rollback_native_build_id": native_build,
            "rollback_native_manifest_sha256": native_manifest_sha,
            "legacy_build_id": manifest["legacy_build_id"],
            "verification": "immutable-manifest-linkage-and-object-presence-size",
            "payload_checksums_revalidated_on_restore": True,
            "objects": witnesses,
        }

    def retirement_step(self, state, name, path, method, body=None):
        from mps.typeahead_refresh import FlyAPIError

        progress = state.setdefault("retirement_progress", {})
        if progress.get(name):
            return
        pending = state.get("pending_action")
        if pending is not None and pending["name"] != name:
            raise RuntimeError("Previous retirement action must be reconciled first")
        if pending is None:
            self.require_release(
                state["route"]["current"],
                state["release_receipt_key"],
                state["release_receipt_sha256"],
            )
            self.service.healthy(state["native_rollback"], require_fresh=False)
            self.guard(state, fresh=True)
            manifest, _ = self.artifact(state["route"]["current"]["build_id"])
            self.retirement_freshness(state["route"]["current"], manifest)
            self.action(
                state, name, method, path + ("/stop" if name == "retire-stop" else ""), body
            )
        deadline = time.monotonic() + min(180, self.timeout)
        while True:
            try:
                observed = self.service.fly("GET", state["app"], path)
                done = observed.get("state") in (
                    ("stopped", "destroyed")
                    if name == "retire-stop"
                    else ("destroyed", "deleted", "pending_destroy")
                )
                if name == "retire-volume" and (
                    observed.get("attached_machine_id") or observed.get("attached_alloc_id")
                ):
                    done = False
            except FlyAPIError as error:
                if error.status_code != 404:
                    raise
                done = True
            if done:
                break
            if time.monotonic() >= deadline:
                raise RuntimeError("Previous retirement action remains unconfirmed")
            time.sleep(0.5)
        progress[name] = True
        self.confirm(state, name + "-confirmed")

    def retirement_freshness(self, target, manifest):
        status = self.service.healthy(target)["index"]
        self.service.tantivy_adapter().installer.validate_runtime(status, manifest)
        return status

    def retire_previous(self, state, key, digest):
        route = state["route"]
        previous = route.get("previous")
        if (
            not previous
            or previous.get("engine") != "tantivy"
            or previous["origin"] != f"https://{state['app']}.fly.dev"
        ):
            return
        if state.get("retired_previous") is not None:
            return
        if not state.get("retire_previous_tantivy"):
            raise RuntimeError("Retained previous Tantivy slot is fenced by default")
        self.require_release(route["current"], key, digest)
        self.guard(state, fresh=True)
        current_manifest, _ = self.artifact(route["current"]["build_id"])
        self.retirement_freshness(route["current"], current_manifest)
        self.service.healthy(state["native_rollback"], require_fresh=False)
        record, bound = self.admission_record(previous)
        if state.get("retirement_intent") is None:
            machines = [
                m
                for m in self.service.fly("GET", state["app"], "machines")
                if m.get("state") != "destroyed"
            ]
            if (
                len(machines) != 1
                or machines[0].get("id") != record["machine_id"]
                or machines[0].get("config", {}).get("metadata", {}).get("owner") != OWNER
                or machines[0]["config"].get("metadata", {}).get("role") != "tantivy-slot"
                or machines[0]["config"].get("mounts")
                != [{"volume": bound["volume_id"], "path": "/data"}]
            ):
                raise RuntimeError("Previous slot has unknown ownership or extra machines")
            if record["machine_id"] in self.service.tantivy_adapter().installer.PROTECTED_MACHINES:
                raise RuntimeError("Protected machine cannot be retired")
            state["retirement_intent"] = {
                "machine_id": record["machine_id"],
                "volume_id": bound["volume_id"],
                "previous": previous,
                "retained": self.retained(previous),
            }
            state["release_receipt_key"] = key
            state["release_receipt_sha256"] = digest
            self.save(state, "retirement-intent")
        intent = state["retirement_intent"]
        self.retirement_step(
            state,
            "retire-stop",
            "machines/" + intent["machine_id"],
            "POST",
            {"signal": "SIGTERM", "timeout": 60},
        )
        self.retirement_step(state, "retire-machine", "machines/" + intent["machine_id"], "DELETE")
        self.retirement_step(state, "retire-volume", "volumes/" + intent["volume_id"], "DELETE")
        state["retired_previous"] = intent
        self.save(state, "previous-retired")

    def prepare(
        self,
        build_id,
        image,
        *,
        request_id=None,
        app=None,
        retire_previous_tantivy=False,
        release_receipt_key=None,
        release_receipt_sha256=None,
    ):
        import fcntl
        from uuid import uuid4

        request_id = request_id or uuid4().hex
        if (
            type(retire_previous_tantivy) is not bool
            or not re.fullmatch(r"[a-f0-9]{32}", request_id)
            or not re.fullmatch(r"registry\.fly\.io/[a-z0-9-]+@sha256:[a-f0-9]{64}", image)
        ):
            raise ValueError("Immutable slot request and image are required")
        lock = (self.root / (request_id + ".lock")).open("a+b")
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            manifest, artifact_raw = self.artifact(build_id)
            state = self.journal(request_id)
            if state is not None:
                if (
                    state.get("schema_version") != 1
                    or state.get("owner") != OWNER
                    or state.get("build_id") != build_id
                    or state.get("image_digest") != image
                    or state.get("artifact_manifest_sha256")
                    != hashlib.sha256(artifact_raw).hexdigest()
                    or state.get("retire_previous_tantivy") != retire_previous_tantivy
                    or (app is not None and state.get("app") != app)
                ):
                    raise ValueError("Preparation resume identity changed")
                if state.get("phase") == "prepared" and state.get("plan_sha256"):
                    plan, _ = self.load_plan(state["plan_sha256"])
                    return {
                        "plan_sha256": state["plan_sha256"],
                        "plan": plan,
                        "routing_target_changed": False,
                        "preparation_receipt": state,
                    }
            else:
                route, _ = self.service.route()
                if route.get("mutation") is not None:
                    raise RuntimeError("Another inactive mutation is pending")
                current = route["current"]
                selected = app or (
                    APPS[1] if current["origin"] == f"https://{APPS[0]}.fly.dev" else APPS[0]
                )
                if selected not in APPS or current["origin"] == f"https://{selected}.fly.dev":
                    raise ValueError("Only an approved inactive Tantivy app may be prepared")
                previous = route.get("previous") or {}
                if (
                    previous.get("origin") == f"https://{selected}.fly.dev"
                    and not retire_previous_tantivy
                ):
                    raise RuntimeError("Retained previous Tantivy slot is fenced by default")
                if previous.get("origin") == f"https://{selected}.fly.dev":
                    self.require_release(current, release_receipt_key, release_receipt_sha256)
                from mps.typeahead_refresh import native_rollback_target, target_engine

                anchor = (
                    current if target_engine(current) == "native" else native_rollback_target(route)
                )
                self.service.healthy(anchor, require_fresh=False)
                status = self.service.healthy(current, require_fresh=False)["index"]
                if manifest["source_actor_count"] < max(1, status.get("actor_count", 0) // 2):
                    raise ValueError("Candidate source regresses the serving corpus floor")
                state = {
                    "schema_version": 1,
                    "owner": OWNER,
                    "request_id": request_id,
                    "build_id": build_id,
                    "app": selected,
                    "region": "ewr",
                    "image_digest": image,
                    "artifact_manifest_sha256": hashlib.sha256(artifact_raw).hexdigest(),
                    "source_build_id": manifest["source_build_id"],
                    "legacy_build_id": manifest["legacy_build_id"],
                    "source_manifest_sha256": manifest["source_manifest_sha256"],
                    "route": route,
                    "native_rollback": anchor,
                    "retire_previous_tantivy": retire_previous_tantivy,
                    "volume_name": "tv_" + request_id[:20],
                    "machine_name": "tv-" + request_id[:20],
                    "pending_action": None,
                    "execution_id": self.service.execution_identity(),
                }
                self.save(state, "planned")
            self.acquire(state)
            self.protect(state)
            self.retire_previous(state, release_receipt_key, release_receipt_sha256)
            bound = self.allocate(state, manifest)
            self.start(state)
            qualified, provider = self.wait_qualified(state, bound)
            self.save(state, "qualified-first-start")
            self.stop(state)
            self.start(state)
            qualified, provider = self.wait_qualified(state, bound)
            self.guard(state)
            admission = qualified["admission"]
            admission_sha = qualified["route"]["admission_sha256"]
            record = {
                "schema_version": 1,
                "admission": admission,
                "deployment_key": state["deployment_key"],
                "deployment_sha256": state["deployment_sha256"],
                "request_id": request_id,
                "machine_id": state["machine_id"],
                "volume_id": state["volume_id"],
                "provider_attestation": provider,
            }
            self.immutable(f"control/tantivy-admissions/{admission_sha}.json", raw_json(record))
            self.release(state)
            route, etag = self.service.route()
            if route != state["route"]:
                raise RuntimeError("Routing changed after candidate qualification")
            plan = {
                "schema_version": 1,
                "owner": OWNER,
                "request_id": request_id,
                "candidate": qualified["route"],
                "expected_route": route,
                "expected_current_etag": etag,
                "native_rollback": state["native_rollback"],
                "deployment_key": state["deployment_key"],
                "deployment_sha256": state["deployment_sha256"],
                "artifact_manifest_sha256": state["artifact_manifest_sha256"],
                "source_manifest_sha256": state["source_manifest_sha256"],
                "app": state["app"],
                "machine_id": state["machine_id"],
                "volume_id": state["volume_id"],
                "image_digest": image,
                "prepared_at": int(time.time()),
                "test_only": admission.get("test_only") is not False,
                "empirical_release_verified": False,
                "promotion_permitted": False,
            }
            raw = raw_json(plan)
            digest = hashlib.sha256(raw).hexdigest()
            self.immutable(f"control/tantivy-plans/{digest}.json", raw)
            state["plan_sha256"] = digest
            self.save(state, "prepared")
            return {
                "plan_sha256": digest,
                "plan": plan,
                "routing_target_changed": False,
                "preparation_receipt": state,
            }
        finally:
            lock.close()

    def load_plan(self, plan_sha256):
        if not isinstance(plan_sha256, str) or not DIGEST.fullmatch(plan_sha256):
            raise ValueError("An exact prepared plan checksum is required")
        raw = self.read(f"control/tantivy-plans/{plan_sha256}.json", 65536)
        if raw is None or hashlib.sha256(raw).hexdigest() != plan_sha256:
            raise ValueError("Prepared plan identity changed or is missing")
        plan = json.loads(raw)
        if plan.get("schema_version") != 1 or plan.get("owner") != OWNER:
            raise ValueError("Invalid prepared plan ownership")
        state = self.journal(plan["request_id"])
        if state is None or state.get("plan_sha256") != plan_sha256:
            raise ValueError("Prepared journal does not bind this plan")
        for key in (
            "app",
            "machine_id",
            "volume_id",
            "deployment_key",
            "deployment_sha256",
            "artifact_manifest_sha256",
            "image_digest",
        ):
            if state.get(key) != plan.get(key):
                raise ValueError("Prepared plan differs from its durable journal")
        return plan, state

    def cancel(self, request_id):
        if not isinstance(request_id, str) or not re.fullmatch(r"[a-f0-9]{32}", request_id):
            raise ValueError("An exact preparation request is required")
        state = self.journal(request_id)
        if state is None:
            raise ValueError("Unknown preparation request")
        if state.get("phase") in ("promoted", "compensated"):
            raise RuntimeError("Published routing transaction cannot be canceled as preparation")
        route, _ = self.service.route()
        if route["current"]["origin"] == f"https://{state['app']}.fly.dev":
            raise RuntimeError("Serving candidate cannot be canceled")
        # A native rollback may have changed the head while retaining our lease.
        if route.get("mutation") is not None and route["mutation"] != state.get("mutation"):
            raise RuntimeError("Another preparation owns the mutation lease")
        state["route"] = {key: value for key, value in route.items() if key != "mutation"}
        self.acquire(state)
        self.protect(state)
        pending = state.get("pending_action")
        if pending is not None and pending["name"] not in ("start", "stop"):
            raise RuntimeError(
                "Provider creation/retirement is unconfirmed; resume the same request"
            )
        if state.get("machine_id"):
            self.stop(state)
        self.release(state)
        self.save(state, "canceled-retained")
        return {
            "request_id": request_id,
            "phase": state["phase"],
            "routing_target_changed": False,
            "retained_machine_id": state.get("machine_id"),
            "retained_volume_id": state.get("volume_id"),
        }

    def promote(
        self, plan_sha256, expected_current_etag, release_receipt_key, release_receipt_sha256
    ):
        from mps.typeahead_refresh import native_rollback_target, target_engine

        plan, state = self.load_plan(plan_sha256)
        if plan.get("test_only") is not False:
            raise ValueError("Test-only preparation cannot be promoted")
        route, etag = self.service.route()
        if expected_current_etag != plan["expected_current_etag"]:
            raise RuntimeError("Prepared plan routing precondition changed")
        if state.get("phase") in (
            "promotion-cas-intent",
            "promotion-cas-confirmed",
            "promoted",
        ) and route == state.get("promotion_target"):
            if (
                state.get("promotion_before") != plan["expected_route"]
                or route.get("current") != plan["candidate"]
                or state.get("promotion_written_etag", etag) != etag
            ):
                raise RuntimeError("Routing transaction recovery identity changed")
            proof = self.require_release(
                plan["candidate"], release_receipt_key, release_receipt_sha256
            )
            self.service.healthy(plan["native_rollback"], require_fresh=False)
            qualified, _ = self.qualify(
                state, json.loads(self.read(state["deployment_key"], 1024 * 1024))
            )
            if any(
                qualified["route"].get(key) != plan["candidate"].get(key)
                for key in ("origin", "build_id", "format", "binary_sha256", "retrieval_strategy")
            ):
                raise RuntimeError("Recovered candidate identity changed")
            state["promotion_written_etag"] = etag
            self.save(state, "promotion-cas-confirmed")
            return self.complete_promotion(
                state, plan_sha256, state["promotion_before"], route, etag, proof
            )
        if state.get("phase") in ("promotion-cas-confirmed", "promoted"):
            raise RuntimeError("Routing changed after confirmed promotion; refusing recovery")
        if (
            route != plan["expected_route"]
            or etag != expected_current_etag
            or etag != plan["expected_current_etag"]
        ):
            raise RuntimeError("Prepared plan routing precondition changed")
        candidate = plan["candidate"]
        proof = self.require_release(candidate, release_receipt_key, release_receipt_sha256)
        self.service.healthy(plan["native_rollback"], require_fresh=False)
        self.acquire(state)
        qualified, _ = self.qualify(
            state, json.loads(self.read(state["deployment_key"], 1024 * 1024))
        )
        if any(
            qualified["route"].get(key) != candidate.get(key)
            for key in ("origin", "build_id", "format", "binary_sha256", "retrieval_strategy")
        ):
            raise RuntimeError("Fresh candidate identity differs from the prepared plan")
        self.guard(state)
        state["release_proof"] = proof
        self.save(state, "promotion-verified")
        # Releasing and replacing use separate ETags. A competing lease invalidates
        # the second CAS, so no leased app can become serving through this path.
        self.release(state)
        actual, cas_etag = self.service.route()
        if actual != route:
            raise RuntimeError("Routing changed before promotion CAS")
        target = {
            "version": 1,
            "current": candidate,
            "previous": route["current"],
            "native_rollback": route["current"]
            if target_engine(route["current"]) == "native"
            else native_rollback_target(route),
        }
        state["promotion_before"] = route
        state["promotion_target"] = target
        state["promotion_etag"] = cas_etag
        self.save(state, "promotion-cas-intent")
        try:
            written_etag = self.service._put_route(target, cas_etag)
        except Exception:
            observed, written_etag = self.service.route()
            if observed != target:
                raise RuntimeError(
                    "Promotion CAS is unconfirmed; routing was not overwritten"
                ) from None
        state["promotion_written_etag"] = written_etag
        self.save(state, "promotion-cas-confirmed")
        return self.complete_promotion(state, plan_sha256, route, target, written_etag, proof)

    def complete_promotion(self, state, plan_sha256, route, target, written_etag, proof):
        from mps.typeahead_refresh import PUBLIC

        candidate = target["current"]
        deadline = time.monotonic() + min(self.timeout, 120)
        try:
            while True:
                observed, current_etag = self.service.route()
                if observed != target or current_etag != written_etag:
                    raise RuntimeError("Routing changed during post-promotion verification")
                try:
                    code, body, headers = self.service.get(
                        PUBLIC, "/xrpc/tech.waow.typeahead.searchActors?q=zzstoatzz.io&limit=5"
                    )
                except (httpx.HTTPError, ValueError):
                    code, body, headers = 503, {}, {}
                if (
                    code == 200
                    and all(
                        headers.get(header) == candidate[key]
                        for key, header in (
                            ("engine", "X-Typeahead-Engine"),
                            ("build_id", "X-Typeahead-Build"),
                            ("format", "X-Typeahead-Format"),
                            ("binary_sha256", "X-Typeahead-Binary-Sha256"),
                            ("retrieval_strategy", "X-Typeahead-Retrieval-Strategy"),
                        )
                    )
                    and any(
                        actor.get("handle") == "zzstoatzz.io" for actor in body.get("actors", [])
                    )
                ):
                    break
                if time.monotonic() >= deadline:
                    raise RuntimeError("Public routing identity/canary verification failed")
                time.sleep(1)
            self.service.healthy(candidate)
        except Exception:
            self.service._restore_previous_route(route, target, written_etag)
            self.save(state, "compensated")
            raise
        self.save(state, "promoted")
        self.unprotect(state)
        return {
            "plan_sha256": plan_sha256,
            "phase": "promoted",
            "route": target,
            "release_proof": proof,
            "public_verified": True,
        }


def run_slot_operation(operation, logger):
    import os

    from mps.typeahead_refresh import Refresh

    if not isinstance(operation, dict):
        raise ValueError("An explicit slot operation object is required")
    name = operation.get("operation")
    fields = {
        "prepare": {
            "build_id",
            "image",
            "request_id",
            "app",
            "retire_previous_tantivy",
            "release_receipt_key",
            "release_receipt_sha256",
        },
        "cancel": {"request_id"},
        "promote": {
            "plan_sha256",
            "expected_current_etag",
            "release_receipt_key",
            "release_receipt_sha256",
        },
    }
    if name not in fields or set(operation) - {"operation"} - fields[name]:
        raise ValueError("Unsupported slot operation or fields")
    service = Refresh(logger)
    try:
        service.execution_identity()
        slots = Slots(service, Path(os.environ["INDEXER_HOME"]) / "tantivy-slot-journals")
        values = {key: value for key, value in operation.items() if key != "operation"}
        return getattr(slots, name)(**values)
    finally:
        service.close()
