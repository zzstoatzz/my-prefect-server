"""Byte-accounted source access through the pinned Typeahead sync client."""

import importlib.util
import sys
from pathlib import Path


def accounted_source(
    sync,
    url,
    token,
    ledger,
    build_id,
    max_bytes,
    *,
    allow_test_loopback=False,
    min_request_interval=0.5,
):
    path = Path(sync.__file__).resolve().with_name("budget.py")
    spec = importlib.util.spec_from_file_location("_mps_pinned_source_budget", path)
    if spec is None or spec.loader is None:
        raise ValueError("Cannot load pinned Typeahead source accounting")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.accounted_source(
        sync,
        url,
        token,
        ledger,
        build_id,
        max_bytes,
        allow_test_loopback=allow_test_loopback,
        min_request_interval=min_request_interval,
    )
