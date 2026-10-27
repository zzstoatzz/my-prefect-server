"""Patch only UI links in the frozen merge wheel, retaining every other member."""

import argparse
import base64
import csv
import hashlib
import io
import json
import zipfile
from pathlib import Path

SOURCE_SHA256 = "15dcb69ab74c54e1dee674f0600bdc1a7211a8966cbfe7f93d2747f6492106c6"
FLOW = "flows/merge_approved.py"


def patch_source(source: str) -> str:
    old = 'UI_RUN_URL = "https://prefect-server.waow.tech/runs/flow-run/{id}"'
    assert source.count(old) == 1
    source = source.replace(
        old,
        'UI_BASE_URL = os.environ.get("PREFECT_UI_URL", "https://prefect-server.waow.tech").rstrip("/")\nUI_RUN_URL = UI_BASE_URL + "/runs/flow-run/{id}"',
    )
    source = source.replace(
        'f"https://prefect-server.waow.tech/artifacts/artifact/',
        'f"{UI_BASE_URL}/artifacts/artifact/',
    )
    source = source.replace(
        'f"\\nreview change: https://prefect-server.waow.tech/artifacts/artifact/',
        'f"\\nreview change: {UI_BASE_URL}/artifacts/artifact/',
    )
    assert source.count("https://prefect-server.waow.tech") == 1
    compile(source, FLOW, "exec")
    return source


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    original = args.source.read_bytes()
    assert hashlib.sha256(original).hexdigest() == SOURCE_SHA256
    with zipfile.ZipFile(io.BytesIO(original)) as wheel:
        members = {name: wheel.read(name) for name in wheel.namelist()}
        record = next(name for name in members if name.endswith(".dist-info/RECORD"))
        patched = {**members, FLOW: patch_source(members[FLOW].decode()).encode()}
        rows = list(csv.reader(io.StringIO(members[record].decode())))
        for row in rows:
            if row[0] == FLOW:
                data = patched[FLOW]
                row[1] = "sha256=" + base64.urlsafe_b64encode(
                    hashlib.sha256(data).digest()
                ).decode().rstrip("=")
                row[2] = str(len(data))
        buffer = io.StringIO(newline="")
        csv.writer(buffer, lineterminator="\n").writerows(rows)
        patched[record] = buffer.getvalue().encode()
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w") as target:
            for entry in wheel.infolist():
                target.writestr(entry, patched[entry.filename])
    result = output.getvalue()
    with zipfile.ZipFile(io.BytesIO(result)) as target:
        assert {name for name in members if members[name] != target.read(name)} == {FLOW, record}
    args.destination.parent.mkdir(parents=True, exist_ok=True)
    if args.destination.exists():
        assert args.destination.read_bytes() == result, "Refusing to replace an immutable wheel"
    else:
        args.destination.write_bytes(result)
    print(
        json.dumps(
            {
                "source_sha256": SOURCE_SHA256,
                "wheel_sha256": hashlib.sha256(result).hexdigest(),
                "changed_members": [FLOW, record],
            }
        )
    )


if __name__ == "__main__":
    main()
