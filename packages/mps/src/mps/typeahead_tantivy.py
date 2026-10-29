"""Cloud publisher integration with the checksum-pinned Typeahead installer."""

from __future__ import annotations

import hashlib
import importlib.util
import re
from pathlib import Path

DIGEST = re.compile(r"[a-f0-9]{64}")


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

    def validate_source(self, manifest: dict, source_raw: bytes) -> dict:
        return self.installer.validate_source_manifest(manifest, source_raw)

    def install(
        self, bundle: Path, destination: Path, manifest: dict, binary: Path, source_manifest: bytes
    ) -> dict:
        self.validate_source(manifest, source_manifest)
        return self.installer.install_bundle(bundle, destination, manifest, binary)

    def freeze(
        self, data: Path, destination: Path, manifest: dict, binary: Path, source_manifest: bytes
    ) -> dict:
        self.validate_source(manifest, source_manifest)
        return self.installer.freeze_bundle(data, destination, manifest, source_manifest, binary)

    def qualify(
        self,
        url: str,
        manifest: dict,
        installation: dict,
        provider_attestation: dict,
        source_manifest: bytes,
        *,
        allow_loopback: bool = False,
    ) -> dict:
        self.validate_source(manifest, source_manifest)
        return self.installer.qualify_runtime(
            url,
            manifest,
            installation,
            allow_loopback=allow_loopback,
            provider_attestation=provider_attestation,
        )
