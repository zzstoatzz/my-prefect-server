import argparse
import base64
import csv
import hashlib
import io
import json
import sysconfig
import zipfile
from pathlib import Path

MODULES = {"mps/pi.py", "mps/pi_local.py", "flows/autofix_policy.py"}
OLD = b"claude-haiku-4-5"
NEW = b"claude-haiku-5-5"


def upgrade(wheel: Path, installed: Path | None = None) -> dict:
    original = wheel.read_bytes()
    with zipfile.ZipFile(io.BytesIO(original)) as archive:
        entries = {item.filename: (item, archive.read(item)) for item in archive.infolist()}
    changed = {}
    for name in MODULES & entries.keys():
        info, before = entries[name]
        after = before.replace(OLD, NEW)
        if before == after:
            continue
        if installed is not None and (installed / name).read_bytes() != before:
            raise ValueError(f"installed module differs from wheel: {name}")
        entries[name] = (info, after)
        changed[name] = after
    if not changed:
        raise ValueError("no Haiku 4.5 policy calls found")
    record = next(name for name in entries if name.endswith(".dist-info/RECORD"))
    rows = io.StringIO(newline="")
    writer = csv.writer(rows, lineterminator="\n")
    for name, (_, content) in entries.items():
        digest = base64.urlsafe_b64encode(hashlib.sha256(content).digest()).decode().rstrip("=")
        writer.writerow(
            [name, f"sha256={digest}", len(content)] if name != record else [name, "", ""]
        )
    entries[record] = (entries[record][0], rows.getvalue().encode())
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        for info, content in entries.values():
            archive.writestr(info, content)
    wheel.write_bytes(output.getvalue())
    if installed is not None:
        for name, content in changed.items():
            (installed / name).write_bytes(content)
        installed_record = installed / record
        installed_rows = list(csv.reader(io.StringIO(installed_record.read_text())))
        for row in installed_rows:
            if row[0] in changed:
                content = changed[row[0]]
                row[1] = "sha256=" + base64.urlsafe_b64encode(
                    hashlib.sha256(content).digest()
                ).decode().rstrip("=")
                row[2] = str(len(content))
        with installed_record.open("w", newline="") as handle:
            csv.writer(handle, lineterminator="\n").writerows(installed_rows)
    return {
        "previous_sha256": hashlib.sha256(original).hexdigest(),
        "wheel_sha256": hashlib.sha256(output.getvalue()).hexdigest(),
        "modules": sorted(changed),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("wheel", type=Path)
    parser.add_argument("--installed", action="store_true")
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    report = upgrade(args.wheel, Path(sysconfig.get_path("purelib")) if args.installed else None)
    args.report.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report))
