import argparse
import fcntl
import importlib.util
import json
import shutil
import time
from pathlib import Path
from types import ModuleType

import duckdb
from mps import analytics


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("candidate", type=Path)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    args.output.mkdir()
    spec = importlib.util.spec_from_file_location("candidate_analytics", args.candidate)
    assert spec and spec.loader
    candidate = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(candidate)
    reference_module = ModuleType("reference_single_thread")
    reference_source = (
        Path(analytics.__file__)
        .read_text()
        .replace(
            "con = duckdb.connect(str(analytics_db))",
            'con = duckdb.connect(str(analytics_db), config={"threads": 1})',
        )
    )
    exec(compile(reference_source, analytics.__file__, "exec"), reference_module.__dict__)
    snapshot = args.output / "spend.jsonl"
    with args.source.open("rb") as src, snapshot.open("wb") as dst:
        fcntl.flock(src, fcntl.LOCK_SH)
        shutil.copyfileobj(src, dst)
        fcntl.flock(src, fcntl.LOCK_UN)
    sample = args.output / "sample.jsonl"
    with snapshot.open() as src:
        lines = [line for _, line in zip(range(1000), src, strict=False)]
    sample.write_text("".join([*lines, *lines[:100]]))
    results = {}
    for name, function, log in (
        ("reference_sample", reference_module.import_spend_log, sample),
        ("bulk_sample", candidate.import_spend_log, sample),
        ("bulk_full", candidate.import_spend_log, snapshot),
    ):
        wall, cpu = time.monotonic(), time.process_time()
        count = function(log, args.output / f"{name}.duckdb")
        results[name] = {
            "rows": count,
            "wall_seconds": time.monotonic() - wall,
            "cpu_seconds": time.process_time() - cpu,
        }
        print(json.dumps({name: results[name]}), flush=True)
    with duckdb.connect(str(args.output / "bulk_sample.duckdb")) as db:
        reference_path = str(args.output / "reference_sample.duckdb").replace("'", "''")
        db.execute(f"ATTACH '{reference_path}' AS reference (READ_ONLY)")
        for left, right in (("main", "reference.main"), ("reference.main", "main")):
            difference = db.execute(
                f"SELECT count(*) FROM (SELECT * FROM {left}.raw_llm_spend EXCEPT ALL SELECT * FROM {right}.raw_llm_spend)"
            ).fetchone()
            assert difference == (0,)
    results["sample_equal"] = True
    (args.output / "result.json").write_text(json.dumps(results, indent=2) + "\n")
    print(json.dumps(results), flush=True)


if __name__ == "__main__":
    main()
