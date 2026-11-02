"""Cloud publisher integration with the checksum-pinned Typeahead installer."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
from pathlib import Path

DIGEST = re.compile(r"[a-f0-9]{64}")


def progress(phase, **metadata):
    print(json.dumps({"phase": phase, **metadata}, sort_keys=True), flush=True)


def durable_receipt(path, value):
    temporary = path.with_suffix(path.suffix + ".partial")
    if path.is_symlink() or temporary.is_symlink():
        raise ValueError("Owned receipt paths cannot be symlinks")
    with temporary.open("wb") as stream:
        stream.write(json.dumps(value, sort_keys=True).encode() + b"\n")
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)
    descriptor = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


class TantivyAdapter:
    def __init__(self, installer: Path, expected_sha256: str):
        installer = Path(installer)
        if (
            not isinstance(expected_sha256, str)
            or not DIGEST.fullmatch(expected_sha256)
            or installer.is_symlink()
        ):
            raise ValueError("An immutable Typeahead installer checksum is required")
        with installer.open("rb") as stream:
            raw = stream.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024 or hashlib.sha256(raw).hexdigest() != expected_sha256:
            raise ValueError("Typeahead installer source checksum mismatch")
        spec = importlib.util.spec_from_file_location("_mps_pinned_tantivy_installer", installer)
        if spec is None or spec.loader is None:
            raise ValueError("Cannot load Typeahead installer")
        module = importlib.util.module_from_spec(spec)
        exec(compile(raw, str(installer), "exec"), module.__dict__)
        self.installer = module
        self.source_sha256 = expected_sha256

    def validate_manifest(self, manifest: dict) -> dict:
        return self.installer.validate_artifact_manifest(manifest, manifest.get("active_build"))

    def validate_source(self, manifest: dict) -> dict | None:
        return self.installer.validate_source_provenance(manifest)

    def install(self, bundle: Path, destination: Path, manifest: dict, binary: Path) -> dict:
        self.validate_source(manifest)
        return self.installer.install_bundle(bundle, destination, manifest, binary)

    def freeze(self, data: Path, destination: Path, manifest: dict, binary: Path) -> dict:
        self.validate_source(manifest)
        return self.installer.freeze_bundle(data, destination, manifest, binary)

    def qualify(
        self,
        url: str,
        manifest: dict,
        installation: dict,
        provider_attestation: dict,
        *,
        allow_loopback: bool = False,
    ) -> dict:
        self.validate_source(manifest)
        return self.installer.qualify_runtime(
            url,
            manifest,
            installation,
            allow_loopback=allow_loopback,
            provider_attestation=provider_attestation,
        )


def fetch_cloud_binary(destination, expected_sha256, cache_root, *, client=None, bucket=None):
    destination, cache_root = Path(destination), Path(cache_root)
    if (
        not isinstance(expected_sha256, str)
        or not DIGEST.fullmatch(expected_sha256)
        or destination.parent != cache_root
        or cache_root.absolute() != cache_root.resolve()
        or destination.is_symlink()
    ):
        raise ValueError("A pinned code artifact in the owned binary cache is required")
    cache_root.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        with destination.open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != expected_sha256:
                raise ValueError("Existing code cache checksum differs")
        return destination
    if client is None:
        import boto3
        from botocore.config import Config

        client = boto3.client(
            "s3",
            endpoint_url=os.environ["INDEX_R2_ENDPOINT"],
            region_name="auto",
            aws_access_key_id=os.environ["INDEX_R2_ACCESS_KEY_ID"],
            aws_secret_access_key=os.environ["INDEX_R2_SECRET_ACCESS_KEY"],
            config=Config(connect_timeout=10, read_timeout=30, retries={"max_attempts": 2}),
        )
        bucket = os.environ["INDEX_R2_BUCKET"]
    key = f"releases/code/tantivy/{expected_sha256}/typeahead-tantivy"
    response = client.get_object(Bucket=bucket, Key=key)
    body = response["Body"]
    part = destination.with_suffix(".partial")
    size = response.get("ContentLength")
    written = 0
    owned_part = False
    digest = hashlib.sha256()
    try:
        if type(size) is not int or not 0 < size <= 128 * 1024**2 or part.is_symlink():
            raise ValueError("Code artifact exceeds its bounded size")
        with part.open("xb") as output:
            owned_part = True
            while chunk := body.read(min(1024 * 1024, size - written + 1)):
                written += len(chunk)
                if written > size:
                    raise ValueError("Code artifact exceeds its declared size")
                output.write(chunk)
                digest.update(chunk)
            output.flush()
            os.fsync(output.fileno())
        if written != size or digest.hexdigest() != expected_sha256:
            raise ValueError("Downloaded code artifact checksum differs")
        with part.open("rb") as source:
            header = source.read(20)
        if len(header) < 20 or header[:6] != b"\x7fELF\x02\x01" or header[18:20] != b"\x3e\x00":
            raise ValueError("Code artifact must be the Linux x86-64 ELF")
        part.chmod(0o755)
        os.link(part, destination)
        descriptor = os.open(cache_root, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        progress(
            "tantivy-code-ready", sha256=expected_sha256, byte_size=written, code_object_key=key
        )
        return destination
    finally:
        body.close()
        if owned_part:
            part.unlink(missing_ok=True)
