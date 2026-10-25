import json
import logging
import os
import uuid

import mps.typeahead_refresh as refresh
import pytest
from botocore.exceptions import ClientError


def test_managed_route_rejects_other_apps_and_builds():
    current = {"origin": refresh.origin(refresh.SLOTS[0]), "build_id": "b1792908655-test"}
    assert refresh.route_target({"version": 1, "current": current}) == current
    for origin in [
        "https://example.com",
        current["origin"] + "/",
        "http://typeahead-startup-20261024.fly.dev",
    ]:
        with pytest.raises(ValueError):
            refresh.route_target({"version": 1, "current": {**current, "origin": origin}})
    with pytest.raises(ValueError):
        refresh.origin("typeahead-search-compact")


@pytest.mark.skipif(
    os.getenv("TYPEAHEAD_REFRESH_TEST_R2") != "1",
    reason="isolated live R2 test requires credentials",
)
def test_real_route_compare_and_swap_and_resource_guard(monkeypatch):
    key = "qualification/refresh-route-" + uuid.uuid4().hex
    monkeypatch.setattr(refresh, "ROUTE_KEY", key)
    artifacts = "qualification/refresh-artifacts-" + uuid.uuid4().hex + "/"
    monkeypatch.setattr(refresh, "ARTIFACT_PREFIX", artifacts)
    service = refresh.Refresh(logging.getLogger(__name__))
    artifact_keys = []
    try:
        origin = refresh.origin(refresh.SLOTS[0])
        code, status, _ = service.get(origin, "/index/status")
        assert code == 200
        route = {
            "version": 1,
            "current": {
                "origin": origin,
                "build_id": status["build_id"],
                "source_watermark": status["source_watermark"],
            },
            "previous": None,
        }
        service.s3.put_object(
            Bucket=service.bucket, Key=key, Body=json.dumps(route).encode(), IfNoneMatch="*"
        )
        fetched, etag = service.route()
        assert fetched == route
        service.serving_guard(fetched, etag, refresh.SLOTS[1])
        with pytest.raises(RuntimeError, match="refusing inactive resource mutation"):
            service.serving_guard(fetched, etag, refresh.SLOTS[0])
        replacement = dict(route, promoted_at=1)
        next_etag = service.replace_route(replacement, etag)
        assert service.route() == (replacement, next_etag)
        with pytest.raises(ClientError) as error:
            service.replace_route(route, etag)
        assert error.value.response["ResponseMetadata"]["HTTPStatusCode"] == 412
        with pytest.raises(RuntimeError, match="Routing changed"):
            service.serving_guard(fetched, etag, refresh.SLOTS[1])
        assert service.route()[0] == replacement
        with pytest.raises(RuntimeError, match="snapshot identity mismatch"):
            service.healthy({**route["current"], "build_id": "b1792908655-does-not-exist"})
        for bid in [
            status["build_id"],
            status["build_id"].removesuffix("-px05"),
            "b1792000000-unused",
            "b1792000000-unused-px05",
        ]:
            key_name = artifacts + bid + "/index.db"
            artifact_keys.append(key_name)
            service.s3.put_object(
                Bucket=service.bucket, Key=key_name, Body=b"isolated artifact retention fixture"
            )
        assert service.prune_artifacts()["deleted_objects"] == 2
        assert service.prune_artifacts()["deleted_objects"] == 0
        retained = service.s3.list_objects_v2(Bucket=service.bucket, Prefix=artifacts)["Contents"]
        assert {item["Key"] for item in retained} == set(artifact_keys[:2])
    finally:
        if artifact_keys:
            service.s3.delete_objects(
                Bucket=service.bucket, Delete={"Objects": [{"Key": key} for key in artifact_keys]}
            )
        service.s3.delete_object(Bucket=service.bucket, Key=key)
        service.close()
